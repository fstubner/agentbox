# Agentbox Household Architecture Spec ($9.99/mo Cloud & Local Hybrid)

## 1. Executive Summary & Value Proposition

The **Agentbox Household** edition is a hybrid private/cloud assistant designed to manage family schedules, chores, home automation, and inbound/outbound communication for $9.99/month. 

- **The Free Tier (Pure Open Source):** Self-hosted, local inference only, requires user to supply SMTP credentials, local calendar, and manual reverse proxying.
- **The $9.99/mo Household Tier (Managed Cloud Hybrid):**
  - Zero-config inbound/outbound email relay (`family@mybox.agentbox.app` or custom domain).
  - High-availability upstream model fallback with prefix prompt caching (DeepSeek V3 / frontier models).
  - Automated encrypted offsite backups (crash-consistent SQLite snapshots).
  - Secure remote tunneling (no port forwarding or static IP required).

---

## 2. Zero-Config Email & Communication Relay

### The Friction in Self-Hosted Email
Requiring a non-technical user to configure SMTP credentials, MX records, SPF, DKIM, and DMARC results in > 80% onboarding drop-off and silent spam-box filtering on outbound replies.

### The Zero-Config Pipeline
```
[External Sender]
       │
       ▼ (MX Record)
[Transactional Cloud Gateway (Postmark / Resend / Agentbox Cloud)]
       │
       ▼ (Encrypted Webhook via TLS / Mutual Auth)
[Agentbox Local Bridge: /v1/inbound/email]
       │
       ▼
[Hermes Message Bus & Ingestion Queue]
       │
       ▼ (Agent reasoning & tool invocation)
[Agentbox Email Tool]
       │
       ▼ (Authenticated HTTP POST with Bearer Token)
[Outbound Relay Gateway] ──(Signed DKIM/SPF)──> [Recipient Inbox]
```

1. **Inbound Path:** 
   - Subscriber receives an assigned address (e.g., `thesmiths@relay.agentbox.app`).
   - Upstream gateway receives the SMTP message, parses MIME parts, redacts attachments over size limits, and dispatches a JSON webhook to the subscriber's Agentbox daemon.
2. **Outbound Path:**
   - Agentbox does not speak SMTP directly. The CLI email bridge executes an HTTP POST to `https://relay.agentbox.app/v1/send` with the user's API token.
   - The relay validates the subscriber's quota, attaches valid cryptographic signatures (DKIM), and delivers with 99.8%+ deliverability.

---

## 3. Prompt Caching Economics (The Math Behind $9.99/mo)

### The Uncached Agent Token Trap
In an agentic system with extensive toolsets, system prompts and schemas dominate token usage:
- Base system instructions + core rules: ~2,500 tokens
- 55 tool schemas & parameter specs: ~5,700 tokens
- **Total Static Prompt:** ~8,200 tokens per invocation.

If billed without caching at standard frontier rates ($3.00 / 1M prompt tokens):
- 100 turns/day $\times$ 8,200 tokens = 820,000 tokens/day
- Cost per day = $2.46
- **Cost per month = $73.80 in API tokens alone.** Fatal for a $9.99/mo subscription.

### The DeepSeek V3 / K-V Prefix Caching Solution
DeepSeek V3 and prefix-caching endpoints bill cache hits at **$0.014 / 1M tokens** (90–95% discount).

$$\text{Daily Cached Prompt Cost} = 820{,}000 \times \frac{\$0.014}{10^6} = \$0.01148/\text{day}$$

$$\text{Monthly Prompt Cost} = \$0.01148 \times 30.5 = \mathbf{\$0.35/\text{month}}$$

Adding dynamic output generation (avg 300 tokens/turn $\times$ 100 turns = 30k tokens/day $\times$ \$0.28 / 1M = \$0.25/month), the **total upstream inference COGS is < \$0.65/month per household**, yielding a **93.5% gross margin on a $9.99/mo subscription**.

### Pipeline Rule: Strict Prefix Freezing
To prevent cache invalidation, prompts must adhere to strict invariant ordering:
1. **[BYTES 0..K - Frozen Prefix]:** Static agent persona, core safety rules, and all 55 tool schemas. No timestamps or session IDs here.
2. **[Dynamic Middle]:** Slowly changing long-term user memories and household preferences.
3. **[Dynamic Tail]:** Current UTC timestamp, session context, chat history, and the latest user turn.

---

## 4. Multi-Tenant Household Security & Separation

Within a single home instance:
1. **Shared vs. Private Memory Scopes:**
   - `scope: household` — visible to all family members (grocery list, home automation, wifi credentials).
   - `scope: private:<user_id>` — confidential personal notes, private appointments, gift ideas.
2. **Role-Based Execution Policies:**
   - Admin (Parents): full access to portal, settings, skill configuration, and unrestricted tool execution.
   - Standard (Family members): interactive tasks, inbox messaging, calendar events.
   - Restricted (Children/Guests): write proposals require Admin approval before committing to Vikunja or state databases.


---

## 5. Automated Remote Tunneling & Appliance Network Topology

### The Friction in Home Network Access
Non-technical household users cannot configure router port-forwarding, navigate carrier-grade NAT (CGNAT), or safely manage dynamic IP updates. A mobile family member at the supermarket requires seamless, secure access to `https://<family>.agentbox.app` without exposing the local network.

### Outbound Reverse Tunnel Architecture
```
[Mobile Phone / Remote Client]
       │
       ▼ (HTTPS :443)
[Agentbox Cloud Gateway Cluster (*.agentbox.app)]
       │ (Wildcard TLS Termination + Token Check)
       ▼ (Encrypted WireGuard / Egress WebSocket Tunnel)
[Local Agentbox Tunnel Container (services/compose/tunnel)]
       │
       ▼ (Loopback HTTP :8771)
[Agentbox Portal]
```

1. **Zero-Configuration Handshake:** Upon box registration with a household subscription token, the local appliance provisions an ephemeral WireGuard keypair and initiates an outbound connection to `relay.agentbox.app`.
2. **Strict Invariant:** No inbound ports on the home router are ever opened. The connection is egress-only.
3. **Session Authentication:** The cloud gateway terminates TLS at the edge, while all application sessions and identity nonces are negotiated directly with the local `agentbox-portal` daemon.
