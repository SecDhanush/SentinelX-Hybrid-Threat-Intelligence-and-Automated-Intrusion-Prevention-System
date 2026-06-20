
# SentinelX: Hybrid Threat Intelligence and Automated Intrusion Prevention Architecture

![SentinelX Architecture](file_00000000a82c71f8b6f4e84a5d...)

### *Autonomous Detection. Intelligent Analysis. Instant Protection.*

---

## 1. Overview
SentinelX is a stateful and autonomous internal threat detection and incident response platform designed to protect local network environments from advanced cyber threats and unauthorized activities. By integrating ARP spoofing defense, real-time IDS event analysis, behavioral threat scoring, automated firewall enforcement, and attacker intelligence enrichment, SentinelX continuously monitors network traffic, identifies malicious behavior, evaluates threat severity, and executes immediate containment actions.

### Core Capabilities:
* **Real-time internal threat detection** using IDS (Suricata)
* **ARP spoofing detection** and automatic mitigation
* **Behavior-based threat scoring** and attacker profiling
* **Automated firewall enforcement** and connection termination
* **Temporary & permanent blocking** with smart unblocking
* **OSINT enrichment** for attacker intelligence
* **Self-healing security** with runtime watchdog monitoring
* **Stateful memory** with tamper-proof database integrity

---

## 2. Objectives
* Detect and respond to internal threats in real-time.
* Prevent unauthorized lateral movement.
* Automatically isolate attackers and malicious hosts.
* Maintain attacker reputation and history for future protection.
* Ensure network trust and integrity by continuously validating gateway authenticity.
* Recover and heal the network automatically after an attack.

---

## 3. Architectural Philosophy
SentinelX is built on a layered security philosophy that combines detection, analysis, decision-making, and automated response. It does not merely alert; it thinks, decides, and acts.

**SENTINELX = DETECTION + INTELLIGENCE + AUTOMATION + ENFORCEMENT + SELF-HEALING**

---

## 4. Core Functional Workflow
1. **Network Monitoring:** Suricata IDS captures and logs all traffic.
2. **Alert Consumption:** SentinelX parses and processes IDS alerts in real-time.
3. **Threat Classification:** Alerts are mapped to attack categories (e.g., Port Scan, SSH Bruteforce, SMB Attack).
4. **Threat Scoring:** Each attacker is assigned a dynamic threat score based on behavior and persistence.
5. **Decision Engine:** Based on score and severity, action is decided (Monitor / Temporary Block / Permanent Block).
6. **Automated Enforcement:** SentinelX updates pfSense firewall, kills active sessions, and blocks the attacker.
7. **Intelligence Enrichment:** For high-risk attackers, OSINT data is fetched (country, ISP, VPN, TOR, abuse score) via AbuseIPDB.
8. **Self-Healing & Watchdog:** Ensures firewall and database integrity at all times and auto-restores missing blocks.

---

## 5. Core Security Layers
* **Network Trust Protection:** Monitors gateway MAC continuously to detect ARP spoofing and trust violations.
* **Immediate Containment:** Blocks attacker MAC, locks gateway MAC, and stops ongoing malicious activity.
* **Network Recovery:** Switches DHCP pool, renews IP, and restores clean network state automatically.
* **Threat Detection:** Suricata detects attacks like scans, bruteforce, exploits, tunneling, and more.
* **Threat Analysis & Scoring:** Analyst-grade scoring model evaluates risk based on frequency and severity.
* **Automated Response:** Firewall enforcement, session termination, and host isolation in real-time.

---

## 6. Technologies Used
* **Languages & Tools:** Python 3, Linux Networking Tools (IP, iptables, dhclient)
* **IDS/SIEM:** Suricata IDS (`eve.json`)
* **Firewall:** pfSense Firewall (`pfctl`)
* **Database & Security:** HMAC-SHA256 (Database Integrity Verification)
* **Threat Intel:** AbuseIPDB (OSINT API Integration)
