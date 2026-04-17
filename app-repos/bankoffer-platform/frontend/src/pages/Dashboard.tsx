import React from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import NotificationBell from "../components/NotificationBell";
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

function fetchOffers(customerId: string): Promise<{ offers: Offer[] }> {
  return fetch(`/api/offers/${customerId}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("auth_token")}` },
  }).then((r) => r.json());
}

const acceptanceData = [
  { month: "Oct", rate: 3.2 },
  { month: "Nov", rate: 4.1 },
  { month: "Dec", rate: 3.8 },
  { month: "Jan", rate: 5.2 },
  { month: "Feb", rate: 6.1 },
  { month: "Mar", rate: 7.4 },
];

export default function Dashboard() {
  const customerId = "cust_001";
  const { data, isLoading, error } = useQuery({
    queryKey: ["offers", customerId],
    queryFn: () => fetchOffers(customerId),
  });

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm" data-testid="dashboard-header">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-2xl font-bold text-gray-900">BankOffer AI</h1>
          <div className="flex items-center gap-4">
            <NotificationBell />
            <button
              onClick={() => { localStorage.removeItem("auth_token"); window.location.href = "/login"; }}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Sign Out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-8 space-y-8">
        {/* Stats row */}
        <div className="grid grid-cols-3 gap-6">
          <div className="bg-white rounded-xl shadow p-6">
            <p className="text-sm text-gray-500">Offer Acceptance Rate</p>
            <p className="text-3xl font-bold text-green-600 mt-1">7.4%</p>
            <p className="text-xs text-green-500 mt-1">+1.3% vs last month</p>
          </div>
          <div className="bg-white rounded-xl shadow p-6">
            <p className="text-sm text-gray-500">Profile Coverage</p>
            <p className="text-3xl font-bold text-blue-600 mt-1">94.2%</p>
            <p className="text-xs text-gray-400 mt-1">Active customer profiles</p>
          </div>
          <div className="bg-white rounded-xl shadow p-6">
            <p className="text-sm text-gray-500">Notifications Sent (7d)</p>
            <p className="text-3xl font-bold text-purple-600 mt-1">12,841</p>
            <p className="text-xs text-gray-400 mt-1">98.7% delivery rate</p>
          </div>
        </div>

        {/* Acceptance trend chart */}
        <div className="bg-white rounded-xl shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Offer Acceptance Rate Trend</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={acceptanceData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" />
              <YAxis unit="%" />
              <Tooltip formatter={(v) => [`${v}%`, "Acceptance Rate"]} />
              <Bar dataKey="rate" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Personalized offers */}
        <div>
          <h2 className="text-lg font-semibold mb-4">Your Personalized Offers</h2>
          {isLoading && <p className="text-gray-500">Loading offers...</p>}
          {error && <p className="text-red-500">Failed to load offers.</p>}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {data?.offers?.map((offer) => (
              <OfferCard key={offer.offer_id} offer={offer} />
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
