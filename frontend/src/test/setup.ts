import { afterAll, afterEach, beforeAll } from "vitest";
import { setupServer } from "msw/node";
import { cleanup } from "@testing-library/react";
import { handlers } from "../mocks/handlers";
import { resetMock } from "../mocks/db";

// jsdom does not implement scrolling.
Element.prototype.scrollTo = () => {};
HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };

export const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { cleanup(); server.resetHandlers(); resetMock(); });
afterAll(() => server.close());
