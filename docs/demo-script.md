# DisputeDesk — demo video script

Target: **under 3 minutes**. Shot in one take in the browser, no slides.

**Setup before recording**

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # so the trace is the real agent
uvicorn app.main:app
```

Open <http://127.0.0.1:8000>, click **Reset demo**, and have the window at ~1440px wide.
Do not pre-select a dispute — start from the inbox.

---

### 0:00 – 0:25 · The problem

> "This is a small shop's PayPal dispute queue. Five open disputes, $693 at risk, and each one
> has a deadline. Handling one properly means pulling the order, the carrier tracking, the
> buyer's messages and any refund you already issued — about 45 minutes. So most sellers either
> refund everything, including money they don't owe, or fight everything and lose."

*On screen: the dashboard. Let the stats row and the five disputes sit for a beat.*

### 0:25 – 1:10 · The agent works

*Click **Stoneware Mug Set of 4** — then **Run agent**.*

> "DisputeDesk reads the same things a careful seller would. The order record, the carrier
> tracking, the buyer's messages, the store's own damaged-goods policy."

*Let the trace fill. Expand one "View what the agent read" so the real JSON shows.*

> "These tools are read-only. The agent can't touch PayPal — it can only look."

### 1:10 – 1:45 · The recommendation

*The recommendation card appears.*

> "One mug of four arrived chipped. The agent doesn't refund the full $62 and it doesn't fight
> a buyer who's plainly right. It offers $15.50 — the value of the one damaged mug — because
> that's what the store's policy says and the buyer already said they don't want to repack the
> set."

*Point at the reasoning list, then the risk flags.*

> "It shows its reasoning, and it tells me what could go wrong: if the buyer declines, the full
> amount is back in play."

### 1:45 – 2:15 · The guardrail

*Change the offer amount to `999.00`, click **Approve & submit**.*

> "Everything here is editable, and nothing has been sent yet. But the seller's money is not
> protected by the model being well-behaved."

*The 422 banner appears.*

> "That rejection is server-side. The offer can't exceed the disputed amount, it has to be an
> offer type PayPal allows for this dispute, and the dispute has to still be open for a
> response. The model doesn't get a vote."

### 2:15 – 2:45 · Approve for real

*Set it back to `15.50`, approve.*

> "Now it goes. Here's the exact call — `POST /v1/customer/disputes/.../make-offer` — the body
> we sent, and PayPal's response. The dispute moves to waiting-for-buyer, and the seller kept
> $46.50 they would otherwise have refunded."

*Point at the Protected stat.*

### 2:45 – 3:00 · The honest case

*Click **Linen Tea Towels** → **Run agent**, and let it land on ACCEPT.*

> "And this is the one that matters. The carrier never collected this parcel — tracking's been
> stuck on 'label created' for three weeks. There's nothing to defend, so the agent says accept
> and refund. An agent that fights everything isn't an agent. It's a liability."

*End on the ACCEPT card.*

---

**If you have 20 seconds spare:** mention that it runs against the real PayPal sandbox with
`PAYPAL_MODE=sandbox`, and that the whole thing works without an API key via the offline rules
engine — the header always says which one is running.

**Things to avoid on camera:** your `.env`, the terminal with the key exported, and the
sandbox credentials in `scripts/sandbox_check.py` output.
