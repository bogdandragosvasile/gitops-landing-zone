/// <reference types="cypress" />

describe("BankOffer AI Dashboard", () => {
  beforeEach(() => {
    cy.intercept("POST", "/api/auth/login").as("login");
    cy.intercept("GET", "/api/offers/*").as("getOffers");
    cy.intercept("POST", "/api/offers/*/accept").as("acceptOffer");
  });

  context("Authentication", () => {
    it("should redirect unauthenticated users to login", () => {
      cy.visit("/dashboard");
      cy.url().should("include", "/login");
    });

    it("should login with valid credentials", () => {
      cy.visit("/login");
      cy.get('[data-testid="email-input"]').type("customer@bank.example.com");
      cy.get('[data-testid="password-input"]').type("SecurePass123!");
      cy.get('[data-testid="login-btn"]').click();
      cy.wait("@login").its("response.statusCode").should("eq", 200);
      cy.url().should("include", "/dashboard");
    });

    it("should show error for invalid credentials", () => {
      cy.visit("/login");
      cy.get('[data-testid="email-input"]').type("wrong@example.com");
      cy.get('[data-testid="password-input"]').type("wrongpassword");
      cy.get('[data-testid="login-btn"]').click();
      cy.get('[data-testid="error-message"]').should("be.visible");
    });
  });

  context("Dashboard Overview", () => {
    beforeEach(() => {
      cy.login("customer@bank.example.com", "SecurePass123!");
      cy.visit("/dashboard");
    });

    it("should display the dashboard header", () => {
      cy.get('[data-testid="dashboard-header"]').should("be.visible");
      cy.contains("Your Personalized Offers").should("be.visible");
    });

    it("should load offers for the logged-in customer", () => {
      cy.wait("@getOffers").its("response.statusCode").should("eq", 200);
      cy.get('[data-testid="offer-card"]').should("have.length.greaterThan", 0);
    });

    it("should show offer cards with required fields", () => {
      cy.wait("@getOffers");
      cy.get('[data-testid="offer-card"]')
        .first()
        .within(() => {
          cy.get('[data-testid="product-name"]').should("be.visible");
          cy.get('[data-testid="personalization-reason"]').should("be.visible");
          cy.get('[data-testid="cta-button"]').should("be.visible");
          cy.get('[data-testid="confidence-badge"]').should("be.visible");
        });
    });

    it("should display notification bell with unread count", () => {
      cy.get('[data-testid="notification-bell"]').should("be.visible");
    });
  });

  context("Offer Interaction", () => {
    beforeEach(() => {
      cy.login("customer@bank.example.com", "SecurePass123!");
      cy.visit("/dashboard");
      cy.wait("@getOffers");
    });

    it("should navigate to offer details when CTA is clicked", () => {
      cy.get('[data-testid="offer-card"]')
        .first()
        .find('[data-testid="cta-button"]')
        .click();
      cy.url().should("match", /\/offers\/.+/);
    });

    it("should allow accepting an offer", () => {
      cy.get('[data-testid="offer-card"]')
        .first()
        .find('[data-testid="cta-button"]')
        .click();
      cy.get('[data-testid="accept-offer-btn"]').click();
      cy.wait("@acceptOffer").its("response.statusCode").should("eq", 200);
      cy.get('[data-testid="success-message"]').should("be.visible");
    });

    it("should allow dismissing an offer", () => {
      cy.get('[data-testid="offer-card"]')
        .first()
        .find('[data-testid="dismiss-btn"]')
        .click();
      cy.get('[data-testid="offer-card"]').should("have.length.lessThan", 4);
    });
  });

  context("Notification Flow", () => {
    beforeEach(() => {
      cy.login("customer@bank.example.com", "SecurePass123!");
      cy.visit("/dashboard");
    });

    it("should open notification dropdown when bell is clicked", () => {
      cy.get('[data-testid="notification-bell"]').click();
      cy.get('[data-testid="notification-dropdown"]').should("be.visible");
    });

    it("should mark notifications as read when viewed", () => {
      cy.get('[data-testid="notification-bell"]').click();
      cy.get('[data-testid="notification-item"]')
        .first()
        .should("have.class", "unread");
      cy.get('[data-testid="mark-all-read-btn"]').click();
      cy.get('[data-testid="notification-item"].unread').should("not.exist");
    });
  });
});
