import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import OfferCard from "../components/OfferCard";

interface Offer {
  offer_id: string;
  product_id: string;
  product_name: string;
  product_type: string;
  personalization_reason: string;
  confidence: number;
  cta_url: string;
}

const PRODUCT_TYPE_LABELS: Record<string, string> = {
  ETF: "ETFs",
  mutual_fund: "Mutual Funds",
  insurance: "Insurance",
  credit: "Credit",
  loan: "Loans",
};

export default function Offers() {
  const customerId = "cust_001";
  const [filter, setFilter] = useState<string>("all");
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<{ offers: Offer[] }>({
    queryKey: ["offers", customerId],
    queryFn: () =>
      fetch(`/api/offers/${customerId}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("auth_token")}` },
      }).then((r) => r.json()),
  });

  const dismissMutation = useMutation({
    mutationFn: (offerId: string) =>
      fetch(`/api/offers/${offerId}/dismiss`, {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("auth_token")}` },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["offers", customerId] }),
  });

  const offers = data?.offers ?? [];
  const filtered = filter === "all" ? offers : offers.filter((o) => o.product_type === filter);
  const productTypes = Array.from(new Set(offers.map((o) => o.product_type)));

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <h1 className="text-2xl font-bold mb-6">All Offers</h1>

      {/* Filter tabs */}
      <div className="flex gap-2 mb-6 flex-wrap">
        <button
          onClick={() => setFilter("all")}
          className={`px-4 py-2 rounded-full text-sm font-medium ${filter === "all" ? "bg-blue-600 text-white" : "bg-white text-gray-600 border"}`}
        >
          All ({offers.length})
        </button>
        {productTypes.map((type) => (
          <button
            key={type}
            onClick={() => setFilter(type)}
            className={`px-4 py-2 rounded-full text-sm font-medium ${filter === type ? "bg-blue-600 text-white" : "bg-white text-gray-600 border"}`}
          >
            {PRODUCT_TYPE_LABELS[type] ?? type} ({offers.filter((o) => o.product_type === type).length})
          </button>
        ))}
      </div>

      {isLoading && <p className="text-gray-500">Loading offers...</p>}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filtered.map((offer) => (
          <OfferCard
            key={offer.offer_id}
            offer={offer}
            onDismiss={() => dismissMutation.mutate(offer.offer_id)}
          />
        ))}
      </div>

      {!isLoading && filtered.length === 0 && (
        <p className="text-center text-gray-400 mt-16">No offers found for this category.</p>
      )}
    </div>
  );
}
