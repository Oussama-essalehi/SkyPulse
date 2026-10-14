import { expect, type Page, type APIRequestContext, test } from "@playwright/test";

/** A callsign currently on the map (works with replay data and live OpenSky data). */
async function liveCallsign(request: APIRequestContext): Promise<string> {
  for (const prefix of ["SKR", "AFR", "BAW", "DLH", "UAL", "DAL", "AAL", "RYR", "EZY", "KLM"]) {
    const response = await request.get(`/api/aircraft?q=${prefix}`);
    if (!response.ok()) continue;
    const hits = (await response.json()) as { callsign: string | null }[];
    const hit = hits.find((h) => h.callsign);
    if (hit?.callsign) return hit.callsign;
  }
  throw new Error("no aircraft with a known callsign prefix on the map");
}

function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // A third-party outage (basemap CDN) is not an app error: the app logs a warning
    // and keeps the live map; the browser still reports the failed request itself.
    const resource = message.location().url;
    const thirdParty = resource && new URL(resource).origin !== new URL(page.url()).origin;
    if (thirdParty && message.text().startsWith("Failed to load resource")) return;
    errors.push(message.text());
  });
  return errors;
}

async function aircraftCount(page: Page): Promise<number> {
  const text = await page.locator(".counter strong").innerText();
  return Number(text.replace(/\s/g, ""));
}

test("live map: aircraft, aircraft card, theme", async ({ page, request }) => {
  const errors = trackErrors(page);
  await page.goto("/");

  // The WebSocket feeds the map: thousands of aircraft in production, hundreds in replay.
  await expect(page.locator(".status")).toContainText("En direct", { timeout: 30_000 });
  await expect.poll(() => aircraftCount(page), { timeout: 60_000 }).toBeGreaterThan(10);
  await expect(page.locator("canvas").first()).toBeVisible();

  // Search -> aircraft card with the flight block and the live facts.
  const callsign = await liveCallsign(request);
  const search = page.getByRole("searchbox", { name: "Rechercher un avion" });
  await search.fill(callsign);
  await page.locator(".search-results button", { hasText: callsign }).first().click();
  const card = page.locator(".aircraft-panel");
  await expect(card.getByRole("heading", { level: 2 })).toHaveText(callsign);
  await expect(card.getByRole("heading", { name: "Vol" })).toBeVisible();
  await expect(card.getByText("Altitude")).toBeVisible();
  await card.getByRole("button", { name: "Fermer" }).click();
  await expect(card).toBeHidden();

  // Theme: dark by default here, grey after the toggle, remembered after a reload.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Passer au thème gris" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "grey");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "grey");
  await expect(page.getByRole("button", { name: "Passer au thème sombre" })).toBeVisible();

  // Side panels open.
  await page.getByRole("button", { name: /Alertes/ }).click();
  await expect(page.getByRole("heading", { name: "Alertes actives" })).toBeVisible();

  expect(errors, errors.join("\n")).toEqual([]);
});

test("airport board: search, live board, aircraft and back", async ({ page, request }) => {
  const errors = trackErrors(page);
  await page.goto("/");
  await expect(page.locator(".status")).toContainText("En direct", { timeout: 30_000 });
  await expect.poll(() => aircraftCount(page), { timeout: 60_000 }).toBeGreaterThan(10);

  // LFPG exists in the reference of the replay (synthetic traffic) and in the real one.
  const search = page.getByRole("searchbox", { name: /aéroport/ });
  await search.fill("LFPG");
  await page.locator(".search-results button.search-airport").first().click();
  const board = page.locator(".airport-panel");
  await expect(board).toContainText("LFPG");
  await expect(board.getByText("Chargement du tableau…")).toBeHidden({ timeout: 20_000 });

  // Open the first tab that lists aircraft (arrivals, departures or on the ground).
  const response = await request.get("/api/airports/LFPG/board");
  expect(response.ok()).toBeTruthy();
  const counts = ((await response.json()) as { counts: Record<string, number> }).counts;
  const tab = (["arrivals", "departures", "ground"] as const).find((t) => counts[t] > 0);
  expect(tab, "no aircraft around LFPG").toBeDefined();
  const label = { arrivals: "Arrivées", departures: "Départs", ground: "Au sol" }[tab!];
  await board.getByRole("tab", { name: label }).click();

  // A row opens the aircraft card, which links back to the board.
  await board.locator(".board-row").first().click();
  const card = page.locator(".aircraft-panel:not(.airport-panel)");
  await expect(card.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(board).toBeHidden();
  await card.getByRole("button", { name: /Tableau/ }).click();
  await expect(board).toContainText("LFPG");

  expect(errors, errors.join("\n")).toEqual([]);
});

test.describe("light system theme", () => {
  test.use({ colorScheme: "light" });

  test("a first visit follows the system: grey theme", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "grey");
    await expect(page.getByRole("button", { name: "Passer au thème sombre" })).toBeVisible();
  });
});
