"""Plain-English explanations for alerts.

Alert *titles* are descriptive but the *messages* are terse numbers
("CPU steal at 12.0%"). These helpers turn an alert into a sentence anyone —
not just an SRE — can act on: what is happening, and why it matters.

Kept channel-agnostic (returns plain text, no markup) so any alerter can use it.
"""
from __future__ import annotations

from .base import Alert, AlertSeverity

# What each severity means, in operator terms.
SEVERITY_MEANING = {
    AlertSeverity.INFO: "informational — no action needed",
    AlertSeverity.WARN: "warning — look into it when convenient",
    AlertSeverity.CRITICAL: "critical — act soon, user impact likely",
    AlertSeverity.EMERGENCY: "emergency — act immediately",
}

# Ordered (keyword-in-title -> explanation). First match wins, so put the more
# specific keywords before the generic ones within a category.
_TITLE_HINTS = [
    # --- CPU ---
    ("CPU Steal", "The AWS hypervisor is giving this VM less CPU than it asked for "
                  "(a 'noisy neighbour' on the same host). Your apps slow down even though "
                  "your own load looks fine — consider a larger or dedicated instance."),
    ("I/O Wait", "The CPU is mostly idle but stuck waiting on the disk. The disk — not the "
                 "CPU — is the bottleneck; expect slow requests."),
    ("System Load", "More work is queued than the CPU cores can process, so requests are "
                    "backing up and latency rises."),
    ("CPU Usage", "The processor is heavily loaded. Apps may slow down, time out, or queue "
                  "requests."),
    # --- Memory ---
    ("OOM Kill", "The kernel ran out of memory and forcibly killed a process — something just "
                 "crashed. Check which service died and why it used so much RAM."),
    ("File Descriptor", "The system is running out of open-file/socket handles. Services will "
                        "soon fail to accept connections or open files."),
    ("Dirty Pages", "A large amount of not-yet-saved data is queued for the disk. A write "
                    "stall or brief freeze may be imminent."),
    ("Swap-Out", "Memory is being pushed to disk right now — active memory pressure. "
                 "Everything gets much slower while this happens."),
    ("Swap", "The system is using disk as overflow RAM, which is far slower than real memory "
             "— a sign it is running low on RAM."),
    ("Memory Usage", "RAM is nearly full. When it runs out the kernel starts killing processes "
                     "(OOM) to survive."),
    # --- Disk ---
    ("Inode", "The disk has free space but is running out of inodes (file slots). New files "
              "can't be created even though 'df' shows space free — usually millions of tiny files."),
    ("Disk Space", "The filesystem is nearly full. At 100% writes fail and databases/apps can "
                   "crash or corrupt data."),
    ("Disk Latency", "The disk is responding slowly. Every read and write is delayed, which "
                     "cascades into app-wide slowness."),
    # --- Network ---
    ("CLOSE_WAIT", "Connections the app should have closed are piling up — usually a socket "
                   "leak bug. Left unchecked it exhausts file descriptors and the app stops "
                   "accepting traffic."),
    ("SYN_RECV", "Many half-open connections are waiting to complete — either a sudden traffic "
                 "spike or a SYN-flood attack."),
    ("Drop", "The network interface is dropping packets. Expect retransmits, added latency, "
             "and reduced throughput."),
    ("Network Error", "The network interface is logging errors. This points at a NIC, cable, "
                      "or driver problem and degrades throughput."),
    ("DNS Resolution", "This host can't resolve hostnames. Any outbound call to another service "
                       "by name will fail until DNS recovers."),
    # --- Process ---
    ("Zombie", "Finished processes aren't being cleaned up by their parent (a buggy parent). "
               "In bulk they exhaust the process table so nothing new can start."),
    ("Disk-Sleep", "Processes are stuck in uninterruptible sleep waiting on I/O — typically a "
                   "hung disk or network mount. They can't even be killed until it clears."),
    # --- EC2 ---
    ("SPOT", "AWS will reclaim this spot instance in about 2 minutes. Drain traffic and "
             "checkpoint any work NOW, or it will be terminated mid-flight."),
    # --- System events ---
    ("Systemd Unit Failed", "A service managed by systemd crashed and is not running. Whatever "
                            "it provided (web server, worker, etc.) is currently down."),
    ("Kernel Critical", "The Linux kernel logged a critical hardware or driver error. "
                        "Investigate for failing hardware, a bad disk, or a driver bug."),
    # --- PSI (pressure stall information) ---
    ("Memory Pressure", "Tasks are stalling while waiting for memory (Linux pressure metric). "
                        "The system is thrashing — a more reliable 'out of memory' signal than raw %."),
    ("I/O Pressure", "Tasks are stalling while waiting for disk I/O (Linux pressure metric). "
                     "The storage layer can't keep up."),
    ("CPU Pressure", "Tasks are stalling while waiting for CPU time (Linux pressure metric) — "
                     "a truer measure of 'overloaded' than raw CPU %."),
    # --- App health ---
    ("Health Check Failed", "A service you asked GuardianD to watch is not responding. Whatever "
                            "depends on it is likely affected."),
]


def impact_hint(alert: Alert) -> str:
    """Return a plain-English 'why it matters' sentence for an alert."""
    if alert.is_recovery:
        return "This condition has cleared — the metric is back within normal limits."
    title = alert.title or ""
    for keyword, hint in _TITLE_HINTS:
        if keyword.lower() in title.lower():
            return hint
    # Category fallback if no title keyword matched.
    return {
        "cpu": "A CPU-related threshold was crossed; processing capacity is constrained.",
        "memory": "A memory-related threshold was crossed; the host is low on RAM.",
        "disk": "A disk-related threshold was crossed; storage capacity or speed is constrained.",
        "network": "A network-related threshold was crossed; connectivity is degraded.",
        "process": "A process-related threshold was crossed; check the process table.",
        "ec2": "An EC2/instance-level event occurred that may affect availability.",
        "system_event": "A system-level event was detected that needs attention.",
        "psi": "The host is under resource pressure (Linux PSI) and tasks are stalling.",
        "app_health": "A monitored service is unhealthy.",
    }.get(alert.category, "A monitored threshold was crossed.")


def severity_meaning(severity: AlertSeverity) -> str:
    """Return a short phrase describing what a severity level implies."""
    return SEVERITY_MEANING.get(severity, "")
