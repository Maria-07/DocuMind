import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

import { FIXTURES } from "./global-setup";

const SHOTS = process.env.E2E_SCREENSHOTS; // optional directory for design-review screenshots
const PASSWORD = "E2e-browser-pass-1";
const unique = Date.now().toString(36);
const admin = { name: "Ada Admin", email: `ada-${unique}@example.com`, org: `E2E Org ${unique}` };

function nav(page: Page, name: string) {
  return page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: true }).click();
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

test.describe.configure({ mode: "serial" });

test("register, upload, index, ask, cite, navigate", async ({ page }) => {
  // --- Register a new organization --------------------------------------------------------
  await page.goto("/register");
  await page.getByLabel("Full name").fill(admin.name);
  await page.getByLabel("Organization").fill(admin.org);
  await page.getByLabel("Work e-mail").fill(admin.email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back, Ada" })).toBeVisible();
  await shot(page, "01-dashboard-empty");

  // --- Upload two real documents via the dialog ------------------------------------------
  await nav(page, "Documents");
  await expect(page.getByText("No documents yet")).toBeVisible();
  const dialog = page.getByRole("dialog");
  // Retry the click: right after navigation the button can render before React has hydrated it.
  await expect(async () => {
    await page.getByRole("button", { name: "Upload documents" }).first().click();
    await expect(dialog).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  await dialog.locator('input[type="file"]').setInputFiles([
    path.join(FIXTURES, "Security Policy.docx"),
    path.join(FIXTURES, "Revenue 2026.xlsx"),
  ]);
  await shot(page, "02-upload-dialog");
  await dialog.getByRole("button", { name: "Upload 2 files" }).click();
  await expect(dialog.getByText("Security Policy.docx")).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();

  // --- Status updates live until both are indexed ------------------------------------------
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "Security Policy" }).getByText("Indexed")).toBeVisible({ timeout: 180_000 });
  await expect(rows.filter({ hasText: "Revenue 2026" }).getByText("Indexed")).toBeVisible({ timeout: 180_000 });
  await shot(page, "03-documents-indexed");

  // --- Document detail shows extracted content with locations ----------------------------
  await page.getByRole("link", { name: /Security Policy/ }).first().click();
  await expect(page.getByText("Passwords must be at least 14 characters", { exact: false })).toBeVisible();
  await expect(page.getByText("Security Policy > Access Control > Passwords").first()).toBeVisible();
  await shot(page, "04-document-detail");

  // --- Chat: streamed, grounded answer with clickable citations ---------------------------
  await nav(page, "AI Chat");
  await expect(page.getByRole("heading", { name: "Ask your documents" })).toBeVisible();
  await page.getByLabel("Message").fill("How often must passwords be changed?");
  await page.getByLabel("Message").press("Enter");
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+$/);
  const chip = page.getByRole("link", { name: /^Source 1:/ }).first();
  await expect(chip).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText(/90 days/).first()).toBeVisible();
  await expect(page.getByText("Sources", { exact: true }).first()).toBeVisible();
  await shot(page, "05-chat-answer");

  // Persisted: reloading the conversation shows the same cited answer (and the session survives
  // a full page reload via the httpOnly refresh cookie).
  await page.reload();
  await expect(page.getByRole("link", { name: /^Source 1:/ }).first()).toBeVisible();

  // Clicking a citation opens the exact cited chunk, highlighted.
  await page.getByRole("link", { name: /^Source 1:/ }).first().click();
  await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+\?chunk=/);
  await expect(page.getByText("Cited", { exact: true })).toBeVisible();
  await shot(page, "06-citation-target");

  // --- Dashboard reflects real counts ---------------------------------------------------
  await nav(page, "Home");
  await expect(page.getByRole("link", { name: /Indexed\s*2/ })).toBeVisible();
  await shot(page, "07-dashboard");

  // --- Admin creates a regular user ----------------------------------------------------
  await nav(page, "User Management");
  await page.getByRole("button", { name: "Add user" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Full name").fill("Uma User");
  await create.getByLabel("E-mail").fill(`uma-${unique}@example.com`);
  await create.getByLabel("Initial password").fill(PASSWORD);
  await create.getByRole("button", { name: "Add user" }).click();
  await expect(page.getByRole("table").getByText("Uma User", { exact: true })).toBeVisible();
  await shot(page, "08-users");

  // --- Sign out ---------------------------------------------------------------------------
  await page.getByRole("button", { name: /Ada Admin/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/documents");
  await expect(page).toHaveURL(/\/login\?next=%2Fdocuments/);
});

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Work e-mail").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Welcome back/ })).toBeVisible();
}

async function choose(page: Page, combobox: string, option: string | RegExp) {
  await page.getByRole("combobox", { name: combobox }).click();
  await page.getByRole("option", { name: option }).click();
}

test("insights: analytics, embeddings, compare, evaluation, sharing", async ({ page }) => {
  test.setTimeout(600_000);
  await login(page, admin.email);

  // --- Analytics: real counts from the first test ---------------------------------------
  await nav(page, "Analytics");
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await expect(page.getByText("2 indexed")).toBeVisible();
  await expect(page.getByText("Document activity", { exact: true })).toBeVisible();
  await shot(page, "13-analytics");

  // --- Embeddings: both documents on the active index ------------------------------------
  await nav(page, "Embeddings");
  await expect(page.getByText("2 / 2")).toBeVisible();
  await expect(page.getByRole("table").getByText("Active", { exact: true })).toBeVisible();
  await shot(page, "14-embeddings");

  // --- Compare two documents (real LLM) --------------------------------------------------
  await nav(page, "Compare");
  await choose(page, "Add document", "Security Policy");
  await choose(page, "Add document", "Revenue 2026");
  await page.getByLabel("Focus question").fill("What does each document cover?");
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader", { name: "Aspect" })).toBeVisible({ timeout: 180_000 });
  await expect(table.getByRole("columnheader", { name: /Security Policy/ })).toBeVisible();
  await shot(page, "15-compare");

  // --- Groups: create one with Uma ---------------------------------------------------------
  await nav(page, "Settings");
  await page.getByLabel("New group name").fill("Finance");
  await page.getByRole("button", { name: "Add" }).last().click();
  await page.getByRole("button", { name: "Members" }).click();
  await page.getByRole("dialog").getByText("Uma User").click();
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("1 member", { exact: true })).toBeVisible();

  // --- Share a document with the group ---------------------------------------------------
  await nav(page, "Documents");
  await page.getByRole("link", { name: /Revenue 2026/ }).first().click();
  await page.getByRole("button", { name: "Share" }).click();
  await choose(page, "Add person or group", "Finance (group)");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Sharing updated")).toBeVisible();
  await page.getByRole("button", { name: "Share" }).click();
  await expect(page.getByRole("dialog").getByText("Finance", { exact: true })).toBeVisible();
  await shot(page, "16-share-dialog");
  await page.keyboard.press("Escape");

  // --- Evaluation: dataset, case with evidence, run on the worker, results ----------------
  await nav(page, "Evaluations");
  await page.getByLabel("Name").fill("Policy QA");
  await page.getByRole("button", { name: "Create dataset" }).click();
  await page.getByRole("link", { name: /Policy QA/ }).click();
  await page.getByLabel("Question").fill("What is the minimum password length?");
  await page.getByLabel("Reference answer (optional)").fill("At least 14 characters.");
  await choose(page, "Add relevant document", "Security Policy");
  await page.getByLabel("Evidence quote").fill("Passwords must be at least 14 characters");
  await page.getByRole("button", { name: "Add case" }).click();
  await expect(page.getByText("Test cases (1)")).toBeVisible();
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByRole("table").getByText("Complete")).toBeVisible({ timeout: 300_000 });
  // The trend refreshes when the polled run completes.
  await expect(page.getByText("Run the dataset to start a trend.")).toHaveCount(0);
  await shot(page, "17-evaluation-dataset");
  await page.getByRole("table").getByRole("link").first().click();
  await expect(page.getByText("Per-case results")).toBeVisible();
  await expect(page.getByText("Precision@5")).toBeVisible();
  await page.getByRole("row", { name: /minimum password length/ }).click();
  await expect(page.getByText("Judge rationale")).toBeVisible();
  await shot(page, "18-evaluation-run");
});

test("a regular user sees only what their role allows", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Work e-mail").fill(`uma-${unique}@example.com`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back, Uma" })).toBeVisible();

  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "User Management" })).toHaveCount(0);
  for (const hidden of ["Analytics", "Embeddings", "Evaluations"]) {
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: hidden })).toHaveCount(0);
  }
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Compare" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload documents" })).toHaveCount(0);
  await page.goto("/users");
  await expect(page.getByText("Administrators only")).toBeVisible();

  // Organization-visible documents are readable; management actions are not offered.
  await page.goto("/documents");
  await expect(page.getByRole("row").filter({ hasText: "Security Policy" })).toBeVisible();
  await page.getByRole("button", { name: /Actions for Security Policy/ }).click();
  await expect(page.getByRole("menuitem", { name: "Download" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Delete" })).toHaveCount(0);
  await shot(page, "09-user-documents");
});

test("mobile layout: collapsible navigation, usable pages", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await page.getByLabel("Work e-mail").fill(admin.email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back, Ada" })).toBeVisible();
  // The desktop sidebar is hidden; navigation lives behind the menu button.
  await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
  await shot(page, "10-mobile-dashboard");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await nav(page, "Documents");
  await expect(page.getByRole("row").filter({ hasText: "Security Policy" })).toBeVisible();
  await shot(page, "11-mobile-documents");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  const filters = page.getByRole("combobox", { name: "Category" });
  const box = await filters.boundingBox();
  expect(box && box.x + box.width).toBeLessThanOrEqual(390); // fully on screen
  await page.goto("/chat");
  await expect(page.getByLabel("Message")).toBeVisible();
  // No horizontal overflow on a phone.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, "12-mobile-chat");
});
