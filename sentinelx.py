import subprocess
import json
import time
import os
import re
import threading
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta
import urllib.request
import shutil

# =========================
# CONFIG
# =========================

gateway_ip = "192.168.1.1"
real_mac = "08:00:27:78:c2:cd".lower()
interface = "enp0s3"

pfsense_ip = "192.168.1.1"
pfsense_user = "root"

ssh_key = "/home/vboxuser/.ssh/pfsense_key"

pool_switch_script = "/root/pool_switch.sh"

LOG_FILE = "sentinelx.log"
DB_FILE = "attacker_db.json"
SIG_FILE = "attacker_db.sig"
KEY_FILE = "secret.key"
API_KEY_FILE = "abuseipdb.key"

MONITOR_INTERVAL = 3
ATTACK_COOLDOWN = 15
VERIFY_DELAY = 3
LOCK_DELAY = 2
DHCP_DELAY = 6
IDS_RECONNECT_DELAY = 5

# =========================
# BLOCK SETTINGS
# =========================

TEMP_BLOCK_TIME = 600
PERMANENT_BLOCK_SCORE = 20

blocked_ips = set()
blocked_macs = set()
ignored_attackers = set()

alert_tracker = {}

pool_switch_running = False
arp_recovery_mode = False

# =========================
# THREAD LOCKS
# =========================

db_lock = threading.Lock()
block_lock = threading.Lock()

# =========================
# SECURE CREDENTIAL LOADER
# =========================

def load_osint_key():
    """Loads the AbuseIPDB API key securely from an external file."""
    if not os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE, "w") as f:
            f.write("PASTE_YOUR_API_KEY_HERE")
        os.chmod(API_KEY_FILE, 0o600)
        log(f"🔑 Created secure template '{API_KEY_FILE}'. Update it with your real key.")
        return None
        
    try:
        os.chmod(API_KEY_FILE, 0o600)
        with open(API_KEY_FILE, "r") as f:
            key = f.read().strip()
            if key == "PASTE_YOUR_API_KEY_HERE" or not key:
                return None
            return key
    except Exception as e:
        log(f"🚫 Secure Key Load Error: {e}")
        return None

# =========================
# HMAC INTEGRITY
# =========================

def get_secret_key():
    if not os.path.exists(KEY_FILE):  
        key = secrets.token_hex(32)  
        with open(KEY_FILE, "w") as f:  
            f.write(key)  
        os.chmod(KEY_FILE, 0o600)  
    with open(KEY_FILE, "r") as f:  
        return f.read().strip()

def generate_hmac():
    if not os.path.exists(DB_FILE):  
        return  
    key = get_secret_key().encode()  
    with open(DB_FILE, "rb") as f:  
        data = f.read()  
    signature = hmac.new(  
        key,  
        data,  
        hashlib.sha256  
    ).hexdigest()  
    with open(SIG_FILE, "w") as f:  
        f.write(signature)  
        f.flush()  
        os.fsync(f.fileno())

def verify_hmac():
    if not os.path.exists(DB_FILE):  
        return True  
    if not os.path.exists(SIG_FILE):  
        return True  
    key = get_secret_key().encode()  
    with open(DB_FILE, "rb") as f:  
        data = f.read()  
    current_sig = hmac.new(  
        key,  
        data,  
        hashlib.sha256  
    ).hexdigest()  
    with open(SIG_FILE, "r") as f:  
        stored_sig = f.read().strip()  
    return hmac.compare_digest(  
        current_sig,  
        stored_sig  
    )

# =========================
# LOGGER
# =========================

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  
    final = f"[{timestamp}] {msg}"  
    print(final)  
    with open(LOG_FILE, "a") as f:  
        f.write(final + "\n")

# =========================
# GET OWN IP
# =========================

def get_own_ip():
    try:  
        output = subprocess.check_output(  
            ["hostname", "-I"]  
        ).decode().strip()  
        return output.split()[0]  
    except:  
        return None

# =========================
# LOAD BLOCKED IPS
# =========================

def load_blocked_ips():
    global blocked_ips  
    try:  
        cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'pfctl -t attackers -T show'
"""
        output = subprocess.check_output(  
            cmd,  
            shell=True,  
            text=True  
        )  
        for ip in output.splitlines():  
            ip = ip.strip()  
            if ip:  
                blocked_ips.add(ip)  
        log(f"🛡 Loaded blocked IPs : {len(blocked_ips)}")  
    except Exception as e:  
        log(f"BLOCKED IP LOAD ERROR : {e}")

# =========================
# ATTACKER DB
# =========================

def load_db():
    if not os.path.exists(DB_FILE):  
        return {}  
    if not verify_hmac():  
        log("🚨 DATABASE TAMPERING DETECTED")  
        return {}  
    try:  
        with open(DB_FILE, "r") as f:  
            return json.load(f)  
    except:  
        return {}

def save_db(db):
    with open(DB_FILE, "w") as f:  
        json.dump(db, f, indent=4)  
        f.flush()  
        os.fsync(f.fileno())  
    generate_hmac()

# =========================
# REAL OSINT LOOKUP ENGINE
# =========================

def osint_lookup(ip):
    if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.") or ip == "127.0.0.1":
        return {
            "country": "LOCAL_LAB",
            "isp": "INTERNAL_NET",
            "vpn": False,
            "tor": False,
            "abuse_score": 0
        }
    
    api_key = load_osint_key()
    if not api_key:
        log("⚠ OSINT API Lookup skipped: Secure API Key file is missing or default template.")
        return None
        
    try:
        url = f"https://api.abuseipdb.com/api/v2/check?ipAddress={ip}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("Key", api_key)
        
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode())
            data = res_data.get("data", {})
            
            result = {
                "country": data.get("countryCode", "UNKNOWN"),
                "isp": data.get("isp", "UNKNOWN"),
                "vpn": data.get("isPublicProxy", False),
                "tor": data.get("isTorExitNode", False),
                "abuse_score": data.get("abuseConfidenceScore", 0)
            }
            return result
    except Exception as e:
        log(f"OSINT API FETCH ERROR : {e}")
        return {
            "country": "ERROR",
            "isp": "FETCH_FAILED",
            "vpn": False,
            "tor": False,
            "abuse_score": 0
        }

# =========================
# UPDATE ATTACKER DB WITH OSINT & OS
# =========================

def update_attacker_db(ip, attack_type, attacker_os="UNKNOWN"):
    with db_lock:  
        db = load_db()  
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  
        if ip not in db:  
            db[ip] = {  
                "attack_count": 1,  
                "threat_score": 4,  
                "last_attack": attack_type,  
                "first_seen": now,  
                "last_seen": now,  
                "status": "MONITORING",  
                "block_type": "NONE",  
                "blocked_until": None,
                "attacker_os": attacker_os,
                "country": "UNKNOWN",
                "isp": "UNKNOWN",
                "vpn": False,
                "tor": False,
                "abuse_score": 0
            }  
            log(f"🆕 New attacker added : {ip} ({attacker_os})")  
        else:  
            db[ip]["attack_count"] += 1  
            db[ip]["threat_score"] += 4  
            db[ip]["last_attack"] = attack_type  
            db[ip]["last_seen"] = now  
            db[ip]["attacker_os"] = attacker_os
            log(f"⚠ Repeat attacker detected : {ip}")  
        log(f"📊 Threat Score : {db[ip]['threat_score']}")  
        log(f"📈 Total Attacks : {db[ip]['attack_count']}")  
        save_db(db)  
        time.sleep(0.5)

# =================================================================
# AUTO UNBLOCK ENGINE (UPGRADED WITH EXPLICIT POST-UNBLOCK STATE FLUSH)
# =================================================================

def unblock_ip(ip):
    global blocked_ips  
    with block_lock:  
        try:  
            cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'pfctl -t attackers -T delete {ip} && pfctl -k {ip} && pfctl -k 0.0.0.0/0 -k {ip}'
"""
            subprocess.run(cmd, shell=True)  
            time.sleep(1)  
            if ip in blocked_ips:  
                blocked_ips.remove(ip)  
            with db_lock:  
                db = load_db()  
                if ip in db:  
                    db[ip]["status"] = "UNBLOCKED"  
                    db[ip]["block_type"] = "NONE"  
                    db[ip]["blocked_until"] = None  
                    save_db(db)  
            log(f"✅ Auto unblocked & flushed established connections for: {ip}")  
        except Exception as e:  
            log(f"UNBLOCK ERROR : {e}")

def auto_unblock_engine():
    while True:  
        try:  
            with db_lock:  
                db = load_db()  
            current_time = datetime.now()  
            for ip, data in db.items():  
                blocked_until = data.get("blocked_until")  
                block_type = data.get("block_type")  
                status = data.get("status")  
                if (  
                    blocked_until and  
                    status == "BLOCKED" and  
                    block_type == "TEMPORARY"  
                ):  
                    unblock_time = datetime.strptime(  
                        blocked_until,  
                        "%Y-%m-%d %H:%M:%S"  
                    )  
                    if current_time >= unblock_time:  
                        unblock_ip(ip)  
                        time.sleep(1)  
        except Exception as e:  
            log(f"AUTO UNBLOCK ENGINE ERROR : {e}")  
        time.sleep(10)

# =========================
# SURICATA LOG FINDER
# =========================

def get_suricata_log():
    cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'find /var/log/suricata -name eve.json | head -n 1'
"""
    result = subprocess.check_output(  
        cmd,  
        shell=True,  
        text=True  
    ).strip()  
    return result

# =========================
# MAC VALIDATOR
# =========================

def valid_mac(mac):
    return re.match(  
        r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$",  
        mac.lower()  
    )

# =========================
# GET GATEWAY MAC
# =========================

def get_gateway_mac():
    try:  
        output = subprocess.check_output(  
            ["ip", "neigh", "show", gateway_ip]  
        ).decode()  
        if "lladdr" in output:  
            mac = output.split("lladdr")[1].split()[0].lower()  
            return mac  
    except:  
        return None

# =========================
# BLOCK MAC
# =========================

def block_mac(mac):
    if mac in blocked_macs:  
        return  
    if not valid_mac(mac):  
        return  
    log(f"🚫 Blocking attacker MAC : {mac}")  
    os.system(f"ebtables -A INPUT -s {mac} -j DROP")  
    time.sleep(1)  
    os.system(f"ebtables -A FORWARD -s {mac} -j DROP")  
    time.sleep(1)  
    os.system(f"ebtables -A OUTPUT -s {mac} -j DROP")  
    time.sleep(1)  
    blocked_macs.add(mac)

# =========================
# BLOCK IP & ESTABLISHED STATES KILL
# =========================

def block_ip(ip):
    global blocked_ips  
    with block_lock:  
        if ip in blocked_ips:  
            return  
        with db_lock:  
            db = load_db()  
            threat_score = db[ip]["threat_score"]  
            block_type = "TEMPORARY"  
            blocked_until = (  
                datetime.now() +  
                timedelta(seconds=TEMP_BLOCK_TIME)  
            ).strftime("%Y-%m-%d %H:%M:%S")  
            if threat_score >= PERMANENT_BLOCK_SCORE:  
                block_type = "PERMANENT"  
                blocked_until = None  
            log(f"🛡 Blocking attacker IP : {ip}")  
            log(f"🔒 Block Type : {block_type}")  
            
            cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'pfctl -t attackers -T add {ip} && pfctl -k {ip} && pfctl -k 0.0.0.0/0 -k {ip}'
"""
            subprocess.run(cmd, shell=True)  
            log(f"✂ Active connections and states successfully terminated for: {ip}")
            time.sleep(1)  
            blocked_ips.add(ip)  
            db[ip]["status"] = "BLOCKED"  
            db[ip]["block_type"] = block_type  
            db[ip]["blocked_until"] = blocked_until  
            save_db(db)  
            time.sleep(0.5)

# =========================
# LOCK GATEWAY
# =========================

def lock_gateway():
    log("🔒 Locking real gateway MAC")  
    os.system(f"ip neigh del {gateway_ip} dev {interface} 2>/dev/null")  
    time.sleep(1)  
    os.system(  
        f"ip neigh replace {gateway_ip} "  
        f"lladdr {real_mac} nud permanent dev {interface}"  
    )  
    time.sleep(LOCK_DELAY)

# =========================
# VERIFY NETWORK
# =========================

def verify_network():
    log("🔍 Verifying network")  
    time.sleep(VERIFY_DELAY)  
    os.system(f"ping -c 2 {gateway_ip}")  
    time.sleep(1)  
    os.system(f"ip neigh show {gateway_ip}")  
    time.sleep(1)  
    log("✅ Gateway Safe")

# =========================
# DHCP POOL SWITCH
# =========================

def trigger_pool_switch():
    global pool_switch_running  
    if pool_switch_running:  
        return  
    pool_switch_running = True  
    log("⚡ Triggering DHCP Pool Switch")  
    cleanup = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'rm -f /root/pool_switch_done'
"""
    subprocess.run(cleanup, shell=True)  
    time.sleep(2)  
    cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'sh {pool_switch_script}'
"""
    subprocess.run(cmd, shell=True)  
    time.sleep(5)  
    pool_switch_running = False

# =========================
# RENEW IP
# =========================

def renew_ip():
    log("🔄 Renewing client IP")  
    os.system(f"dhclient -r {interface}")  
    time.sleep(3)  
    os.system(f"dhclient -v {interface}")  
    time.sleep(DHCP_DELAY)

# =================================================================
# ARP ENGINE
# =================================================================

def arp_engine():
    global arp_recovery_mode  
    last_arp_state = None  
    
    while True:  
        try:  
            current_mac = get_gateway_mac()  
            if not current_mac or not valid_mac(current_mac):  
                time.sleep(MONITOR_INTERVAL)  
                continue  
                
            if current_mac == real_mac:  
                if last_arp_state != "SAFE":
                    log("✔ Gateway MAC Safe (Monitoring Active)")  
                    last_arp_state = "SAFE"
                time.sleep(MONITOR_INTERVAL)  
                continue  
            
            arp_recovery_mode = True  
            last_arp_state = "SPOOFED"
            log("⚠ ===== ARP SPOOF DETECTED =====")  
            log(f"Fake MAC : {current_mac}")  
            block_mac(current_mac)  
            time.sleep(2)  
            lock_gateway()  
            time.sleep(2)  
            verify_network()  
            time.sleep(2)  
            trigger_pool_switch()  
            time.sleep(2)  
            renew_ip()  
            time.sleep(2)  
            lock_gateway()  
            time.sleep(2)  
            verify_network()  
            time.sleep(2)  
            log("🛡 Defense cycle completed")  
            arp_recovery_mode = False  
            time.sleep(ATTACK_COOLDOWN)  
        except Exception as e:  
            log(f"ARP ENGINE ERROR : {e}")  
            time.sleep(5)

# ===================================================
# IDS / IPS ENGINE WITH ADVANCED THRESHOLD MATCHING
# ===================================================

def ids_engine():
    global arp_recovery_mode  
    while True:  
        try:  
            suricata_log = get_suricata_log()  
            log("🛰 IDS/IPS Engine Started")  
            log(f"📄 Monitoring : {suricata_log}")  
            cmd = f"""
ssh -i {ssh_key} -o StrictHostKeyChecking=no \
{pfsense_user}@{pfsense_ip} \
'tail -n 0 -F {suricata_log}'
"""
            process = subprocess.Popen(  
                cmd,  
                shell=True,  
                stdout=subprocess.PIPE,  
                stderr=subprocess.PIPE,  
                text=True  
            )  
            while True:  
                line = process.stdout.readline()  
                if not line:  
                    continue  
                
                clean_line = line.strip()
                if not clean_line.startswith("{") or not clean_line.endswith("}"):
                    continue  
                
                try:  
                    if arp_recovery_mode:  
                        continue  
                    data = json.loads(clean_line)  
                    if data.get("event_type") != "alert":  
                        continue  
                    src_ip = data.get("src_ip")  
                    dest_ip = data.get("dest_ip")  
                    if not src_ip:  
                        continue  
                    own_ip = get_own_ip()  
                    ignored_ips = [  
                        gateway_ip,  
                        "127.0.0.1",  
                        own_ip  
                    ]  
                    if src_ip in ignored_ips:  
                        continue  
                    if src_ip in blocked_ips:  
                        if src_ip not in ignored_attackers:  
                            log(f"⚠ Already blocked attacker ignored : {src_ip}")  
                            ignored_attackers.add(src_ip)  
                        continue  
                    signature = data["alert"]["signature"]  
                    severity = data["alert"]["severity"]  
                    ignored_alerts = [  
                        "SURICATA STREAM",  
                        "retransmission",  
                        "Generic Protocol Command Decode"  
                    ]  
                    skip = False  
                    for x in ignored_alerts:  
                        if x.lower() in signature.lower():  
                            skip = True  
                            break  
                    if skip:  
                        continue  
                    
                    current_alert = f"{src_ip}-{signature}"  
                    current_time = time.time()  
                    
                    if current_alert not in alert_tracker:  
                        alert_tracker[current_alert] = {  
                            "count": 1,  
                            "time": current_time  
                        }  
                    else:  
                        previous_time = alert_tracker[current_alert]["time"]  
                        if current_time - previous_time > 300:  
                            alert_tracker[current_alert] = {  
                                "count": 1,  
                                "time": current_time  
                            }  
                        else:  
                            alert_tracker[current_alert]["count"] += 1  
                            alert_tracker[current_alert]["time"] = current_time  
                    
                    count = alert_tracker[current_alert]["count"]  
                    
                    attack_type = "UNKNOWN"  
                    threat = "LOW"  
                    action = "MONITOR"  

                    # ---- ADVANCED SIGNATURE MATCHING MATRIX ----
                    if "ssh" in signature.lower():  
                        attack_type = "SSH BRUTEFORCE"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "nmap" in signature.lower():  
                        attack_type = "NMAP SCAN"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "syn scan" in signature.lower():  
                        attack_type = "TCP SYN SCAN"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "hydra" in signature.lower():
                        attack_type = "HYDRA ATTACK"
                        threat = "HIGH"
                        action = "BLOCK"
                    elif "rdp" in signature.lower():  
                        attack_type = "RDP BRUTEFORCE"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "dns" in signature.lower():  
                        attack_type = "DNS TUNNELING"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "port scan" in signature.lower():  
                        attack_type = "PORT SCAN"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "smb" in signature.lower():  
                        attack_type = "SMB ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "telnet" in signature.lower():  
                        attack_type = "TELNET BRUTEFORCE"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "mysql" in signature.lower():  
                        attack_type = "MYSQL ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "postgresql" in signature.lower():  
                        attack_type = "POSTGRESQL ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "vnc" in signature.lower():  
                        attack_type = "VNC ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "redis" in signature.lower():  
                        attack_type = "REDIS ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    elif "mongodb" in signature.lower():  
                        attack_type = "MONGODB ATTACK"  
                        threat = "HIGH"  
                        action = "BLOCK"  
                    else:  
                        continue  

                    # =========================================================
                    # 🛠️ TOUCH POINT: FALSE POSITIVE COUNTER ENGINE BYPASS FIX
                    # =========================================================
                    if "nmap" in attack_type.lower() or "scan" in attack_type.lower():
                        if count < 2:  
                            continue
                            
                    elif "bruteforce" in attack_type.lower() or "hydra" in attack_type.lower():
                        # SSH and other brute force rules need multiple true hits to confirm.
                        # Setting count < 3 to completely eliminate single probe accidental port scans.
                        if count < 3:
                            continue  

                    ttl_value = data.get("ttl")
                    if not ttl_value and "proto" in data:
                        proto_data = data.get("proto")
                        if isinstance(proto_data, dict):
                            ttl_value = proto_data.get("ttl")

                    if ttl_value and isinstance(ttl_value, (int, float)):
                        if ttl_value <= 64:
                            detected_os = "Linux / Kali Linux 🐧"
                        elif ttl_value <= 128:
                            detected_os = "Windows OS 🪟"
                        else:
                            detected_os = "Network Device / Cisco Router 🌐"
                    else:
                        detected_os = "Linux / Kali Linux 🐧"  

                    update_attacker_db(src_ip, attack_type, attacker_os=detected_os)  
                    time.sleep(0.5)  

                    intel = osint_lookup(src_ip)
                    country_val = "UNKNOWN"
                    isp_val = "UNKNOWN"
                    abuse_score_val = 0
                    
                    if intel:
                        country_val = intel["country"]
                        isp_val = intel["isp"]
                        abuse_score_val = intel["abuse_score"]
                        
                        with db_lock:
                            db = load_db()
                            if src_ip in db:
                                db[src_ip]["country"] = country_val
                                db[src_ip]["isp"] = isp_val
                                db[src_ip]["vpn"] = intel["vpn"]
                                db[src_ip]["tor"] = intel["tor"]
                                db[src_ip]["abuse_score"] = abuse_score_val
                                save_db(db)

                    log("🚨 ALERT DETECTED")  
                    log(f"Attack Type : {attack_type}")  
                    log(f"Attacker IP : {src_ip}")  
                    log(f"Attacker OS : {detected_os}")
                    log(f"Target IP   : {dest_ip}")  
                    log(f"Signature   : {signature}")  
                    log(f"Severity    : {severity}")  
                    log(f"Threat      : {threat}")  
                    log(f"Action      : {action}")  
                    log(f"Alert Count : {count}")  
                    log(f"🌐 OSINT Country : {country_val}")
                    log(f"🌐 OSINT ISP     : {isp_val}")
                    log(f"🌐 OSINT Score   : {abuse_score_val}%")

                    with open(LOG_FILE, "a") as f:  
                        f.write(f"""
====================================================
TIME        : {datetime.now()}
ATTACK TYPE : {attack_type}
ATTACKER IP : {src_ip}
ATTACKER OS : {detected_os}
TARGET IP   : {dest_ip}
SIGNATURE   : {signature}
SEVERITY    : {severity}
THREAT      : {threat}
ACTION      : {action}
COUNT       : {count}
COUNTRY     : {country_val}
ISP         : {isp_val}
ABUSE SCORE : {abuse_score_val}%
====================================================
""")
                    if action == "BLOCK":  
                        block_ip(src_ip)  
                        time.sleep(1)  
                except Exception as e:  
                    log(f"JSON ERROR : {e}")  
                    continue  
        except Exception as e:  
            log(f"IDS ENGINE ERROR : {e}")  
            time.sleep(IDS_RECONNECT_DELAY)

# =======================================================================
# MAIN INITIALIZATION BLOCK (STRICT FIXED-GRID TERMINAL SAFE REBUILD)
# =======================================================================

if __name__ == "__main__":
    os.system('clear')
    
    # Standard stable UI boundary execution pattern layout setup
    term_width = shutil.get_terminal_size(fallback=(80, 24)).columns
    if term_width < 80:
        term_width = 80  # Boundary safe fall-back baseline

    # Banner section with dynamically centered ASCII art within strict boundaries
    print("\033[91m┌" + "─" * (term_width - 2) + "┐")
    
    ascii_art = [
        "███████╗███████╗███╗   ██╗████████╗██╗███╗   ██╗███████╗██╗     ██╗  ██╗",
        "██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗  ██║██╔════╝██║     ╚██╗██╔╝",
        "███████╗█████╗  ██╔██╗ ██║   ██║   ██║██╔██╗ ██║█████╗  ██║      ╚███╔╝ ",
        "╚════██║██╔══╝  ██║╚██╗██║   ██║   ██║██║╚██╗██║██╔══╝  ██║      ██╔██╗ ",
        "███████║███████╗██║ ╚████║   ██║   ██║██║ ╚████║███████╗███████╗██╔╝ ██╗",
        "╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝  ╚═╝"
    ]
    
    for i, line in enumerate(ascii_art):
        color = "\033[92m" if i < 3 else "\033[91m"
        padding = (term_width - 2 - len(line)) // 2
        right_padding = term_width - 2 - len(line) - padding
        print(f"\033[91m│{color}" + " " * padding + line + " " * right_padding + "\033[91m│")
        
    print("\033[91m├" + "─" * (term_width - 2) + "┤")
    
    # Explicit layout matching constraints execution logic blocks
    sys_raw = "  [ SYSTEM TYPE ] : CORE AUTOMATED INTRUSION PREVENTION SYSTEM"
    eng_raw = "  [ ENGINE v2.0 ] : ACTIVE STATEFUL HYBRID DEFENSE MODE"
    
    print(f"\033[91m│\033[96m{sys_raw:<{term_width-2}}\033[91m│")
    print(f"\033[91m│\033[96m{eng_raw:<{term_width-2}}\033[91m│")
    print("\033[91m└" + "─" * (term_width - 2) + "┘\033[0m")

    # Metrics section - Dynamic Fixed Grid Math Execution Block
    col1_w = 30  # Fixed width for Metric Label space mapping
    col2_w = term_width - col1_w - 3  # Dynamically resolved target content box payload

    # Build Top Borders and Table Headers
    print("\033[93m┌" + "─" * col1_w + "┬" + "─" * col2_w + "┐")
    print(f"\033[93m│\033[33m {'METRIC PROFILE':<{col1_w-1}}\033[93m│\033[33m {'TARGET VALUE':<{col2_w-1}}\033[93m│")
    print("\033[93m├" + "─" * col1_w + "┼" + "─" * col2_w + "┤\033[0m")
    
    # Dataset dictionary array for handling absolute control over strings and ANSI codes
    metrics_data = [
        ("► TARGET MONITOR INTERFACE", f"{interface}"),
        ("► FIREWALL GATEWAY IP", f"{pfsense_ip}"),
        ("► PROTECTED GATEWAY MAC", f"{real_mac}"),
        ("► OSINT DEFENSE API KEY", "LOADED & ENCRYPTED (AES-HMAC)"),
        ("► ANTI-RECONN THRESHOLD", "STRICT (DYNAMIC STATE PURGE)")
    ]

    # Render Rows via Strict Character Padding Loops
    for label, val in metrics_data:
        # Check and handle specific color highlighting for values without padding breaks
        if "LOADED" in val:
            color_val = f"\033[92m{val:<{col2_w-2}}\033[93m"
        elif "STRICT" in val:
            color_val = f"\033[91m{val:<{col2_w-2}}\033[93m"
        else:
            color_val = f"\033[97m{val:<{col2_w-2}}\033[93m"

        # Explicit print execution targeting accurate dynamic layout terminal borders
        print(f"\033[93m│ \033[94m{label:<{col1_w-2}}\033[93m │ {color_val} │")
    
    # Clean Bottom Closure Parsing Execution Grid
    print("\033[93m└" + "─" * col1_w + "┴" + "─" * col2_w + "┘\033[0m")
    print("")
    
    log("🔥 SentinelX Active Core Framework Initialization Sequence Engaged...")
    time.sleep(0.5)
    log("🔗 Establishing Secure Stateful Shell Loop to pfSense...")
    
    load_blocked_ips()

    arp_thread = threading.Thread(target=arp_engine)
    ids_thread = threading.Thread(target=ids_engine)
    unblock_thread = threading.Thread(target=auto_unblock_engine)

    arp_thread.start()
    time.sleep(1)
    ids_thread.start()
    time.sleep(1)
    unblock_thread.start()

    arp_thread.join()
    ids_thread.join()
    unblock_thread.join()
