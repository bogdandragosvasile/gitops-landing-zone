# headscale

Self-hosted [Headscale](https://github.com/juanfont/headscale) control plane for the [gitops-landing-zone](https://github.com/bogdandragosvasile/gitops-landing-zone), deployed via the app-of-apps pattern.

Headscale is an open-source, self-hostable implementation of the Tailscale coordination server. You point the official Tailscale clients (phone, laptop, etc.) at your Headscale server and they form a WireGuard mesh — no Tailscale account, no paid tier, no phoning home.

## Layout

```
headscale/
├── manifests/
│   ├── namespace.yaml
│   ├── configmap.yaml       # config.yaml
│   ├── pvc.yaml             # SQLite DB + keys persistence
│   ├── deployment.yaml
│   ├── service.yaml         # ClusterIP (API) + LoadBalancer (DERP UDP 3478)
│   └── ingress.yaml         # http://headscale.local
└── scripts/
    ├── create-user.sh       # headscale users create <name>
    └── create-preauth.sh    # headscale preauthkeys create --user <name>
```

## Deploy

ArgoCD picks this up via `gitops-infra/apps/headscale.yaml` — the root app-of-apps materialises it on its own. To deploy on a fresh cluster:

1. Push this repo to Gitea as `platform/headscale`.
2. Confirm the repo secret is registered in ArgoCD (see `gitops-infra/manifests/argocd/values.yaml`).
3. Sync the `headscale` Application in ArgoCD.

## First-run setup

```bash
# 1. Create a user for your phone
bash scripts/create-user.sh alice

# 2. Generate a pre-auth key (reusable, valid for a week)
bash scripts/create-preauth.sh alice --reusable --expiry 168h
# → copy the printed key
```

## Registering a phone

1. Install the **Tailscale** app (App Store / Play Store).
2. Configure the custom login server:
   - **iOS**: Settings → Use alternate server → `http://headscale.local`.
     Requires an MDM profile or the MDM-provisioned flow on most OS versions.
   - **Android**: Settings → Change server → `http://headscale.local`.
3. In the app, tap **Sign in with auth key** and paste the key from step 2 above.

The phone now has a `100.x.x.x` Tailscale IP and a `*.landingzone.local` Magic DNS name. It can reach any node registered with the same Headscale instance.

## Reaching the landing zone backend from the phone

For the phone to reach services like `portal.local` or `gitea.local:3000`, **register the laptop as a Tailscale node too**, using that laptop as a **subnet router**:

```bash
# On the landing-zone host:
brew install tailscale   # or download from https://tailscale.com/download
sudo tailscale up \
  --login-server http://headscale.local \
  --auth-key <another-preauth-key> \
  --advertise-routes 172.20.0.0/24
# Then on Headscale, approve the route:
bash scripts/approve-route.sh <node-name> 172.20.0.0/24
```

Now the phone's traffic to `172.20.0.100` (the MetalLB IP behind `*.local` hostnames) is tunnelled via the laptop → the k3d cluster.

## Phone is on a different network (4G / 5G / different WiFi)

The embedded DERP relay in Headscale only helps if both peers can reach the Headscale server. On a LAN-only landing zone, phones on cellular can't reach `headscale.local` directly.

Two free options:

1. **[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)** (recommended): deploy `cloudflared` as a sibling Application, expose `headscale.local` at `https://headscale.your-domain.com`. Unlimited, free for dev, no paid tier required. Update `server_url` in the ConfigMap to the public URL.
2. **Public VPS** ($5/mo is not "free" — skip unless you already have one): run Headscale there instead, treat this one as a dev-only instance.

## Admin commands

All `headscale` CLI commands run inside the pod:

```bash
kubectl -n headscale exec -it deploy/headscale -- headscale --help

# common ones:
kubectl -n headscale exec deploy/headscale -- headscale users list
kubectl -n headscale exec deploy/headscale -- headscale nodes list
kubectl -n headscale exec deploy/headscale -- headscale preauthkeys list --user alice
kubectl -n headscale exec deploy/headscale -- headscale routes list
kubectl -n headscale exec deploy/headscale -- headscale routes enable --route <id>
```

## License

Headscale itself is BSD-3-Clause. This configuration and the helper scripts inherit whatever licence the parent `gitops-landing-zone` repo uses (MIT).
