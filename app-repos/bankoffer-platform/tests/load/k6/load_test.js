import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("errors");
const offerLatency = new Trend("offer_latency_ms");

export const options = {
  scenarios: {
    constant_load: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 200,
      maxVUs: 600,
    },
  },
  thresholds: {
    http_req_duration: ["p(99)<300"],
    errors: ["rate<0.01"],
    offer_latency_ms: ["p(99)<300"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN || "test-load-token";

const CUSTOMER_IDS = Array.from({ length: 100 }, (_, i) => `cust_${String(i + 1).padStart(4, "0")}`);

export function setup() {
  console.log(`Load test starting against ${BASE_URL}`);
  console.log(`Target: 500 RPS for 60s, p99 < 300ms`);
}

export default function () {
  const customerId = CUSTOMER_IDS[Math.floor(Math.random() * CUSTOMER_IDS.length)];

  const params = {
    headers: {
      Authorization: `Bearer ${AUTH_TOKEN}`,
      "Content-Type": "application/json",
    },
    tags: { endpoint: "get_offers" },
  };

  const start = Date.now();
  const res = http.get(`${BASE_URL}/offers/${customerId}`, params);
  const duration = Date.now() - start;

  offerLatency.add(duration);

  const success = check(res, {
    "status is 200": (r) => r.status === 200,
    "response has offers": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.offers);
      } catch {
        return false;
      }
    },
    "response time < 300ms": (r) => r.timings.duration < 300,
  });

  errorRate.add(!success);
  sleep(0.001);
}

export function teardown(data) {
  console.log("Load test complete");
}
