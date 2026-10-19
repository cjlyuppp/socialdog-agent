import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { setupServer } from "msw/node";
import { cleanup } from "@testing-library/react";
import { handlers } from "../mocks/handlers";
import { resetMock } from "../mocks/db";

// Node's native web storage is distinct from the jsdom browser environment.
vi.stubGlobal("localStorage", (globalThis as unknown as { jsdom: { window: Window } }).jsdom.window.localStorage);

// jsdom does not implement scrolling.
Element.prototype.scrollTo = () => {};
window.matchMedia = () => ({ matches: false, media: "", onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } });
HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };

export const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { cleanup(); server.resetHandlers(); localStorage.clear(); resetMock(); });
afterAll(() => server.close());
