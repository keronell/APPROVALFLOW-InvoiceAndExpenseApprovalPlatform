import { useState } from "react";
import { jsonFetch } from "../api.js";
import { SAMPLES } from "../samples.js";

const CATEGORIES = ["meals", "travel", "saas", "hardware", "other"];

const EMPTY_LINE = { description: "", quantity: "1", unitPrice: "" };

// Form state keeps numbers as strings (inputs); toPayload converts on submit.
function toForm(sample) {
  return {
    id: sample.id ?? "",
    submitter: sample.submitter ?? "",
    department: sample.department ?? "",
    vendor: sample.vendor ?? "",
    vendorKnown: !!sample.vendorKnown,
    invoiceNumber: sample.invoiceNumber ?? "",
    currency: sample.currency ?? "USD",
    category: sample.category ?? "meals",
    attendees: sample.attendees != null ? String(sample.attendees) : "",
    lineItems: (sample.lineItems ?? []).map((li) => ({
      description: li.description ?? "",
      quantity: String(li.quantity ?? 1),
      unitPrice: String(li.unitPrice ?? 0),
    })),
    taxAmount: sample.taxAmount != null ? String(sample.taxAmount) : "0",
    total: sample.total != null ? String(sample.total) : "",
    receiptPresent: !!sample.receiptPresent,
    date: sample.date ?? "",
    notes: sample.notes ?? "",
  };
}

function toPayload(form) {
  const payload = {
    id: form.id,
    submitter: form.submitter,
    department: form.department,
    vendor: form.vendor,
    vendorKnown: form.vendorKnown,
    invoiceNumber: form.invoiceNumber,
    currency: form.currency || "USD",
    category: form.category,
    lineItems: form.lineItems.map((li) => ({
      description: li.description,
      quantity: Number(li.quantity),
      unitPrice: Number(li.unitPrice),
    })),
    taxAmount: Number(form.taxAmount || 0),
    total: Number(form.total),
    receiptPresent: form.receiptPresent,
    date: form.date,
  };
  if (form.attendees !== "") payload.attendees = Number(form.attendees);
  if (form.notes !== "") payload.notes = form.notes;
  return payload;
}

function lineItemsPlusTax(form) {
  const items = form.lineItems.reduce(
    (sum, li) => sum + (Number(li.quantity) || 0) * (Number(li.unitPrice) || 0), 0,
  );
  return items + (Number(form.taxAmount) || 0);
}

export default function SubmitView({ onSubmitted }) {
  const [form, setForm] = useState(toForm(Object.values(SAMPLES)[0]));
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const set = (field) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [field]: value }));
  };

  const setLine = (index, field) => (e) => {
    setForm((f) => {
      const lineItems = f.lineItems.map((li, i) =>
        i === index ? { ...li, [field]: e.target.value } : li,
      );
      return { ...f, lineItems };
    });
  };

  const addLine = () => setForm((f) => ({ ...f, lineItems: [...f.lineItems, { ...EMPTY_LINE }] }));
  const removeLine = (index) =>
    setForm((f) => ({ ...f, lineItems: f.lineItems.filter((_, i) => i !== index) }));

  const fillSample = (sample) => {
    setForm(toForm(sample));
    if (jsonMode) setJsonText(JSON.stringify(sample, null, 2));
    setResult(null);
    setError("");
  };

  // JSON mode lets fixtures be posted as-is (incl. test hooks like test.failPayment).
  const toggleJsonMode = () => {
    setError("");
    if (!jsonMode) {
      setJsonText(JSON.stringify(toPayload(form), null, 2));
      setJsonMode(true);
    } else {
      try {
        setForm(toForm(JSON.parse(jsonText)));
        setJsonMode(false);
      } catch (e) {
        setError(`Fix the JSON before switching back to the form: ${e.message}`);
      }
    }
  };

  const submit = async () => {
    setError("");
    setResult(null);
    let body;
    if (jsonMode) {
      body = jsonText;
    } else {
      try {
        body = JSON.stringify(toPayload(form));
      } catch (e) {
        setError(String(e.message));
        return;
      }
    }
    try {
      const accepted = await jsonFetch("/api/invoices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      setResult(accepted);
      onSubmitted(accepted.trackingId);
    } catch (e) {
      setError(String(e.message));
    }
  };

  const computed = lineItemsPlusTax(form);
  const total = Number(form.total);
  const totalMismatch =
    !jsonMode && form.total !== "" && Number.isFinite(total) && Math.abs(computed - total) > 0.005;

  return (
    <section>
      <div className="section-head">
        <h2>Submit an invoice / expense</h2>
        <button className="chip" onClick={toggleJsonMode}>
          {jsonMode ? "Back to form" : "Edit as JSON"}
        </button>
      </div>
      <div className="samples">
        {Object.entries(SAMPLES).map(([label, sample]) => (
          <button key={label} className="chip" onClick={() => fillSample(sample)}>
            {label}
          </button>
        ))}
      </div>

      {jsonMode ? (
        <textarea rows={18} value={jsonText} onChange={(e) => setJsonText(e.target.value)} />
      ) : (
        <div className="form">
          <div className="form-grid">
            <label className="field">
              <span>Invoice id</span>
              <input value={form.id} onChange={set("id")} placeholder="INV-1001" />
            </label>
            <label className="field">
              <span>Invoice number</span>
              <input value={form.invoiceNumber} onChange={set("invoiceNumber")} placeholder="NW-INV-7781" />
            </label>
            <label className="field">
              <span>Submitter email</span>
              <input value={form.submitter} onChange={set("submitter")} placeholder="you@company.example" />
            </label>
            <label className="field">
              <span>Department (budget)</span>
              <input value={form.department} onChange={set("department")} placeholder="engineering-2026Q2" />
            </label>
            <label className="field">
              <span>Vendor</span>
              <input value={form.vendor} onChange={set("vendor")} />
            </label>
            <label className="field">
              <span>Category</span>
              <select value={form.category} onChange={set("category")}>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Date</span>
              <input type="date" value={form.date} onChange={set("date")} />
            </label>
            <label className="field">
              <span>Attendees (optional)</span>
              <input type="number" min="1" value={form.attendees} onChange={set("attendees")} />
            </label>
            <label className="field">
              <span>Currency</span>
              <input value={form.currency} onChange={set("currency")} />
            </label>
            <div className="field checks">
              <label className="check">
                <input type="checkbox" checked={form.vendorKnown} onChange={set("vendorKnown")} />
                <span>Known vendor</span>
              </label>
              <label className="check">
                <input type="checkbox" checked={form.receiptPresent} onChange={set("receiptPresent")} />
                <span>Receipt attached</span>
              </label>
            </div>
          </div>

          <div className="line-items">
            <div className="line-head">
              <span>Line items</span>
              <button className="chip" onClick={addLine}>+ Add line</button>
            </div>
            {form.lineItems.map((li, i) => (
              <div className="line-row" key={i}>
                <input className="line-desc" placeholder="description"
                  value={li.description} onChange={setLine(i, "description")} />
                <input className="line-num" type="number" min="0" placeholder="qty"
                  value={li.quantity} onChange={setLine(i, "quantity")} />
                <input className="line-num" type="number" min="0" step="0.01" placeholder="unit price"
                  value={li.unitPrice} onChange={setLine(i, "unitPrice")} />
                <button className="chip danger" onClick={() => removeLine(i)} title="Remove line">✕</button>
              </div>
            ))}
          </div>

          <div className="form-grid">
            <label className="field">
              <span>Tax amount</span>
              <input type="number" min="0" step="0.01" value={form.taxAmount} onChange={set("taxAmount")} />
            </label>
            <label className="field">
              <span>Total</span>
              <input type="number" min="0" step="0.01" value={form.total} onChange={set("total")} />
            </label>
          </div>
          {totalMismatch && (
            <p className="note">
              Heads up: line items + tax come to ${computed.toFixed(2)}, but the total
              says ${total.toFixed(2)}. The total is what gets checked against policy.
            </p>
          )}

          <label className="field">
            <span>Notes (optional)</span>
            <textarea rows={2} value={form.notes} onChange={set("notes")}
              placeholder="Anything the approver should know" />
          </label>
        </div>
      )}

      <button className="primary" onClick={submit}>Submit</button>
      {result && (
        <p className="note">
          Accepted — tracking id <code>{result.trackingId}</code> (status: {result.status})
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
