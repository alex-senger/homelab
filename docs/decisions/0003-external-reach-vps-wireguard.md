# 3. External reach via a rented VPS over plain WireGuard, cluster-side as an in-cluster pod

Date: 2026-09-08

## Status

Accepted

## Context

Sub-project #4 needs `https://<subdomain>.senger-solutions.com` reachable from the public
internet without opening a port on the home router. Two separate decisions sit inside that goal.

**Getting traffic to the home network at all.** Sub-project #1 sketched a Tailscale subnet router
as the plausible answer: install a Tailscale node in the cluster, advertise the LAN range, and
reach it from anywhere via the tailnet. That sketch was never built. It also means every external
request depends on a third-party control plane (Tailscale's coordination servers) staying up and
staying trusted, and it terminates traffic on the tailnet rather than in front of the Gateway that
already owns TLS.

**Where the cluster end of that tunnel lives.** Talos is a locked-down, API-managed OS with no
general-purpose host networking story — no package manager, no arbitrary `iptables`/`nftables`
rules, no way to run a WireGuard interface on the host and then forward and NAT its traffic to a
Service. A host-level tunnel would also have to land on one specific node, making that node a
second, uglier single point of failure on top of the one this ADR already accepts.

## Decision

**(a) A rented VPS, reached over plain WireGuard, with the cluster dialing out.** The VPS holds no
certificate material and does no TLS — it is an L4/SNI passthrough relay in front of the tunnel.
The cluster initiates the WireGuard connection outbound to the VPS, so the home router needs no
inbound port forward and no public IP awareness. This was chosen over the Tailscale
subnet-router sketch from sub-project #1: plain WireGuard has no dependency on a third-party
tailnet control plane, and cluster-initiated dialing achieves the same "no inbound home port"
property without delegating trust to it.

**(b) The cluster end of the tunnel is an in-cluster WireGuard pod, not a Talos host interface.**
A pod is ordinary Kubernetes config — a Deployment, a Secret for the private key, a Service —
portable and reviewable the same way as everything else in this repository. It can run on any
node (node-agnostic ingress, matching how the rest of the platform is scheduled), and it can do
the forward/NAT-to-Gateway step in userspace or via standard Kubernetes networking, which Talos's
locked-down host networking cannot do without breaking the "immutable, API-managed, no SSH"
property that is the reason Talos was chosen in the first place (sub-project #2).

## Consequences

As of this ADR, the LAN half of sub-project #4 is implemented and verified: Cilium Gateway API
(v1.3.0 CRDs), LB-IPAM and L2 announcement of the reserved `192.168.178.201–.220` range,
cert-manager with Cloudflare DNS-01, and a Gateway serving a wildcard certificate all work end to
end on the LAN. `https://whoami.senger-solutions.com` resolves and serves a valid certificate from
inside the network today.

The VPS and the in-cluster `wg-ingress` pod described by this ADR are not yet built. Until they
are, there is no path from the public internet into the cluster — this repository does not yet
deliver external reach, only the in-cluster half of it. `docs/runbooks/external-reach-recovery.md`
tracks the remaining work as the pending VPS phase.

Once built, the VPS is a single point of failure — but only for **external** reach. LAN access via
the Gateway's LoadBalancer IP (`192.168.178.201`) does not route through the VPS or the tunnel at
all, so a VPS outage degrades the cluster from "reachable from anywhere" to "reachable from the
LAN," not to "unreachable." The VPS also holds no certificate material and does no TLS, so its
compromise does not expose the wildcard private key — the blast radius of losing it is limited to
being able to see (but not decrypt or forge certificates for) the traffic it relays, and to denying
external service.

The tradeoff against the rejected Tailscale sketch: WireGuard alone gives no mesh, no ACLs, and no
NAT traversal beyond what is configured by hand here — Tailscale would have supplied all three for
free. Rejected in favor of not depending on a third party for something this repository's own
config can express directly.
