/// <reference types="cypress" />

Cypress.Commands.add("login", (email: string, password: string) => {
  cy.session([email, password], () => {
    cy.request({
      method: "POST",
      url: "/api/auth/login",
      body: { email, password },
    }).then((response) => {
      window.localStorage.setItem("auth_token", response.body.token);
    });
  });
});

declare global {
  namespace Cypress {
    interface Chainable {
      login(email: string, password: string): Chainable<void>;
    }
  }
}
