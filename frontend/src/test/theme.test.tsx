import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Shell } from "../components/ui";

// The Shell polls /health; answer it so no test touches a real server.
beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 })),
  );
});
afterEach(() => vi.unstubAllGlobals());

const renderShell = () =>
  render(
    <MemoryRouter>
      <Shell>page</Shell>
    </MemoryRouter>,
  );

describe("theme switch", () => {
  it("offers only Light and Dark, with no System option", () => {
    renderShell();
    const group = screen.getByRole("group", { name: "Colour theme" });
    const labels = Array.from(group.querySelectorAll("button")).map((b) => b.textContent);
    expect(labels).toEqual(["Light", "Dark"]);
  });

  it("follows the OS until a choice is made, and stores nothing", () => {
    document.documentElement.setAttribute("data-theme", "dark"); // as set by the pre-paint script
    renderShell();
    expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("ds-theme")).toBeNull();
  });

  it("applies and remembers the analyst's choice", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("ds-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Light" }).getAttribute("aria-pressed")).toBe("false");
  });
});
