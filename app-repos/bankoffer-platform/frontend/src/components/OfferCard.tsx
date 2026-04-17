import React from "react";

interface Offer {
  offer_id: string;
  product_id: string;
  product_name: string;
  product_type: string;
  personalization_reason: string;
  confidence: number;
  cta_url: string;
}

interface OfferCardProps {
  offer: Offer;
  onDismiss?: () => void;
}

const PRODUCT_TYPE_COLORS: Record<string, string> = {
  ETF: "bg-blue-100 text-blue-700",
  mutual_fund: "bg-indigo-100 text-indigo-700",
  insurance: "bg-green-100 text-green-700",
  credit: "bg-orange-100 text-orange-700",
  loan: "bg-purple-100 text-purple-700",
};

function ConfidenceBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? "bg-green-500" : pct >= 60 ? "bg-yellow-500" : "bg-gray-400";
  return (
    <span
      data-testid="confidence-badge"
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-white ${color}`}
    >
      {pct}% match
    </span>
  );
}

export default function OfferCard({ offer, onDismiss }: OfferCardProps) {
  const typeColor = PRODUCT_TYPE_COLORS[offer.product_type] ?? "bg-gray-100 text-gray-700";

  return (
    <div data-testid="offer-card" className="bg-white rounded-xl shadow hover:shadow-md transition-shadow p-6 flex flex-col gap-3">
      <div className="flex justify-between items-start">
        <span className={`text-xs font-semibold px-2 py-1 rounded-full ${typeColor}`}>
          {offer.product_type.replace("_", " ").toUpperCase()}
        </span>
        <ConfidenceBadge score={offer.confidence} />
      </div>

      <h3 data-testid="product-name" className="text-lg font-semibold text-gray-900">
        {offer.product_name}
      </h3>

      <p data-testid="personalization-reason" className="text-sm text-gray-600 flex-1">
        {offer.personalization_reason}
      </p>

      <div className="flex gap-2 mt-2">
        <a
          data-testid="cta-button"
          href={offer.cta_url}
          className="flex-1 text-center py-2 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          Learn More
        </a>
        {onDismiss && (
          <button
            data-testid="dismiss-btn"
            onClick={onDismiss}
            className="py-2 px-3 border border-gray-200 rounded-lg text-sm text-gray-500 hover:bg-gray-50"
            aria-label="Dismiss offer"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
