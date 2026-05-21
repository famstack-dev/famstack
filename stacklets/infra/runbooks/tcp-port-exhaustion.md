# Outbound TCP fails instantly with `Can't assign requested address`

## Symptom

New outbound TCP connections from the host fail immediately (not after a timeout) with `EADDRNOTAVAIL` / "Can't assign requested address". `ping 8.8.8.8` works. AdGuard logs DoH timeouts at exactly 30s. Multiple unrelated services on the host degrade at the same time.

Typical user-visible signal: `DNS_PROBE_FINISHED_*` in the browser on cable-attached devices.

## Quick diagnosis

```bash
# How many sockets are stuck in TIME_WAIT?
netstat -an | grep -c TIME_WAIT

# Top destinations of those stuck sockets
netstat -an -p tcp | grep TIME_WAIT \
  | awk '{print $5}' | sort | uniq -c | sort -rn | head -10
```

Interpret:

- Count in the thousands, especially near or above 16,000 (macOS ephemeral range is 49152-65535, ~16k slots): **port exhaustion confirmed**. The dominant destination identifies the offending caller.
- Count under a few hundred: not port exhaustion. Look elsewhere.

To name the live offender while the storm is in progress:

```bash
sudo lsof -nP -iTCP:<dominant-port> -r 1 2>/dev/null
```

The `-r 1` repeats every second, so short-lived connections show up.

## Cause

A local process is opening TCP connections faster than the kernel drains `TIME_WAIT`. The Mac runs out of source ports. Every new `connect()` on the host then fails with `EADDRNOTAVAIL`. Anything that needs a new outbound socket (AdGuard DoH queries, Caddy origin fetches, scripts using `curl`) fails until the storm stops and TIME_WAIT drains.

Common code-level origin: an HTTP client that creates a fresh `requests.Session` (or equivalent) per call, called on a polling loop. The Session goes out of scope at the end of each call, the connection closes, the kernel parks it in TIME_WAIT.

## Fix

1. Identify the destination address that dominates the TIME_WAIT list. The owning process is the culprit.
2. Stop it:
   - Docker: `docker stop <name>`
   - Host process: `kill <PID>` (find with `lsof` as above)
3. Watch the count drain:
   ```bash
   while true; do echo "$(date +%H:%M:%S)  $(netstat -an | grep -c TIME_WAIT)"; sleep 5; done
   ```
4. If the count does **not** drop within a few minutes with the source stopped, the TCP state machine is wedged. **Reboot is required.** Force-shortening MSL (`sudo sysctl -w net.inet.tcp.msl=1`) will not help once timers stop firing on existing entries.
5. After recovery, fix the client that was churning sockets. Typical patch: move `requests.Session()` into the calling object's `__init__` and reuse it; or set explicit `Connection: keep-alive` and a connection pool.

## Prevent recurrence

Leave a steady-state monitor running so the next storm is caught before it wedges the host:

```bash
while true; do
  TW=$(netstat -an | grep -c TIME_WAIT)
  TOP=$(netstat -an -p tcp | grep TIME_WAIT \
        | awk '{print $5}' | sort | uniq -c | sort -rn \
        | head -3 | tr '\n' '|')
  printf "%s  TIME_WAIT=%-6s  top: %s\n" "$(date +%H:%M:%S)" "$TW" "$TOP"
  sleep 5
done
```

Alert when `TW` crosses a low threshold (e.g. 5000) via `osascript -e 'display notification ...'` or a webhook to a chat bot.

Defensive sysctl tuning, persisted via `/Library/LaunchDaemons/local.sysctl.plist`:

```bash
sudo sysctl -w net.inet.ip.portrange.first=32768   # ~32k ports vs default 16k
sudo sysctl -w net.inet.tcp.msl=5000                # 10s TIME_WAIT vs default 30s
```

These do not fix the root cause; they raise the ceiling and lower the drain time so a misbehaving client has more headroom before wedging the host.
