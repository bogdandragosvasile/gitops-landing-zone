import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

const errorRate = new Rate("errors");

export const options = {
  scenarios: {
    spike: {
      executor: "ramping-arrival-rate",
      startRate: 50,
      timeUnit: "1s",
      preAllocatedVUs: 300,
      maxVUs: 1200,
      stages: [
        { duration: "10s", target: 50 },
        { duration: "30s", target: 1000 },
        { duration: "20s", target: 1000 },
        { duration: "10s", target: 50 },
        { duration: "10s", target: 50 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<1000"],
    errors: ["rate<0.05"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN || "test-spike-token";
const CUSTOMER_IDS = Array.from({ length: 500 }, (_, i) => `cust_${String(i + 1).padStart(4, "0")}`);

export default function () {
  const customerId = CUSTOMER_IDS[Math.floor(Math.random() * CUSTOMER_IDS.length)];

  const res = http.get(`${BASE_URL}/offers/${customerId}`, {
    headers: {
      Authorization: `Bearer ${AUTH_TOKEN}`,
      "Content-Type": "application/json",
    },
  });

  const ok = check(res, {
    "status is 200 or 429 (rate limited)": (r) => r.status === 200 || r.status === 429,
    "not 500": (r) => r.status !== 500,
  });

  errorRate.add(res.status === 500);
}
