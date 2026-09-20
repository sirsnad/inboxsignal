Classify one email into exactly one type, from its subject and the first 500 characters of its body, and pull out the few structured fields the type carries.

Types:
- receipt: a purchase confirmation or payment receipt
- tracking: a shipment status update (shipped, out for delivery, delivered)
- alert: something that may need action to avoid a bad outcome (fraud confirmation, "was this you" security alert, payment due)
- statement: a periodic account statement or bill summary
- verification: a code or link to verify/confirm identity or an address, often with an expiry
- notice: informational with no action (rate change, terms update, projection, "your data was shared")
- offer: marketing or a promotion
- digest: recurring content roundup (newsletter, job alerts, listing alerts)
- personal: written by a human to this person
- other: none of the above

Respond with only a JSON object. Include the optional fields only when the email states them; never guess.
{
  "email_type": "<one of the types>",
  "confidence": 0.0-1.0,
  "merchant": "<who was paid, for receipt/statement>",
  "amount": "<decimal string like 84.98, for receipt/statement>",
  "source": "<payment source if named: PayPal, Venmo, card ending XXXX, ...>",
  "carrier": "<UPS/FedEx/USPS/... for tracking>",
  "status": "<short shipment status for tracking: shipped, out for delivery, delivered, ...>",
  "eta": "<stated delivery estimate verbatim, for tracking>",
  "delivered": true/false,
  "summary": "<one line of what this email says, max ~80 chars, for heads-up rows>",
  "action_url": "<for alert/verification: the email's primary confirm/review URL, verbatim>"
}
