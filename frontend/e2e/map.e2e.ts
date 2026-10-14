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

test("live map: aircraft, aircraft card", async ({ page, request }) => {
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

  // Side panels open.
  await page.getByRole("button", { name: /Alertes/ }).click();
  await expect(page.getByRole("heading", { name: "Alertes actives" })).toBeVisible();

  expect(errors, errors.join("\n")).toEqual([]);
});

test("legend above the map, airport filter with its weather", async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto("/");
  await expect(page.locator(".status")).toContainText("En direct", { timeout: 30_000 });
  await expect.poll(() => aircraftCount(page), { timeout: 60_000 }).toBeGreaterThan(10);

  // The altitude legend stays above the map (aircraft are drawn below it).
  const legendOnTop = await page.evaluate(() => {
    const legend = document.querySelector(".legend")!.getBoundingClientRect();
    const hit = document.elementFromPoint(legend.x + legend.width / 2, legend.y + 8);
    return Boolean(hit?.closest(".legend"));
  });
  expect(legendOnTop).toBe(true);

  // Airport filter: only the aircraft of LFPG, and its weather.
  await page.getByRole("button", { name: "Filtres" }).click();
  await page.getByRole("button", { name: "Aéroport" }).click();
  await page.getByRole("searchbox", { name: "Choisir un aéroport" }).fill("LFPG");
  await page.locator(".airport-filter-results button").first().click();
  await expect(page.locator(".airport-filter")).toContainText(/\d+ avions? : arrivées/, {
    timeout: 20_000,
  });
  // Weather: decoded METAR, or why there is none (CI runs without outbound calls).
  await expect(
    page
      .locator(".airport-filter")
      .getByText(/Météo|Pas de bulletin|Météo désactivée/)
      .first(),
  ).toBeVisible({ timeout: 20_000 });
  expect(await aircraftCount(page)).toBeLessThan(500);
  await page.getByRole("button", { name: "Réinitialiser" }).click();
  await expect.poll(() => aircraftCount(page)).toBeGreaterThan(10);
  await page.getByRole("button", { name: "Fermer" }).first().click();

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

test("assistant: conversation in the right sidebar, follow-ups keep the context", async ({
  page,
}) => {
  const errors = trackErrors(page);
  // The model is not part of the e2e stack: answer like the assistant service does.
  const bodies: { question: string; history: { role: string; content: string }[] }[] = [];
  await page.route("**/api/assistant/ask", async (route) => {
    const body = route.request().postDataJSON() as (typeof bodies)[number];
    bodies.push(body);
    const offTopic = body.question.includes("gravité");
    await route.fulfill({
      json: {
        answer: offTopic
          ? "Je réponds uniquement aux questions sur le trafic aérien…"
          : "42 avions.",
        sql: null,
        columns: offTopic ? [] : ["zone", "avions_total"],
        rows: offTopic ? [] : [["France", 42]],
        off_topic: offTopic,
      },
    });
  });
  await page.goto("/");

  await page.getByRole("button", { name: "Assistant" }).click();
  const chat = page.getByRole("complementary", { name: "Assistant" });
  await expect(chat).toBeVisible();
  const box = chat.getByRole("textbox", { name: "Votre question" });

  await box.fill("c'est quoi l'équation de gravité ?");
  await box.press("Enter");
  await expect(chat.locator(".chat-off-topic")).toContainText("uniquement");

  await box.fill("Combien d'avions en France ?");
  await box.press("Enter");
  await expect(chat.locator(".chat-assistant").last()).toContainText("42 avions.");
  await box.fill("et en Espagne ?");
  await box.press("Enter");
  await expect(chat.locator(".chat-user")).toHaveCount(3);
  await expect(chat.locator(".chat-assistant:not(.chat-typing)")).toHaveCount(3);

  // The follow-up carries the previous exchange, not the off-topic one.
  expect(bodies[2].history).toEqual([
    { role: "user", content: "Combien d'avions en France ?" },
    { role: "assistant", content: "42 avions." },
  ]);
  // Closing keeps the conversation.
  await chat.getByRole("button", { name: "Fermer l'assistant" }).click();
  await expect(chat).toBeHidden();
  await page.getByRole("button", { name: "Assistant" }).click();
  await expect(chat.locator(".chat-user")).toHaveCount(3);

  expect(errors, errors.join("\n")).toEqual([]);
});
