const fs = require("fs");
const path = require("path");

const { chromium } = require("playwright");

const targetUrl =
  "https://quvideo.feishu.cn/wiki/LCOAwSdYNixFtlk0dAicrueRndh?sheet=e5973a";
const outDir = path.resolve("downloads/feishu_e5973a_capture");
const responseDir = path.join(outDir, "responses");

fs.mkdirSync(responseDir, { recursive: true });

function safeName(input) {
  return input.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 180);
}

function looksUseful(url, text) {
  return (
    /sheet|wiki|drive|bitable|space|docx|base|table|grid|cell/i.test(url) ||
    /https?:\/\/|audio|mp3|wav|AA|长音频|筛选/.test(text)
  );
}

(async () => {
  const context = await chromium.launchPersistentContext(
    path.resolve(".tmp/feishu-profile-e5973a"),
    {
      headless: false,
      executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      viewport: { width: 1500, height: 1000 },
      args: ["--no-sandbox", "--disable-crash-reporter", "--disable-crashpad"],
    },
  );

  const page = context.pages()[0] || (await context.newPage());
  let responseIndex = 0;

  page.on("response", async (response) => {
    try {
      const url = response.url();
      const contentType = response.headers()["content-type"] || "";
      if (!/json|text|javascript|protobuf|octet-stream/i.test(contentType)) {
        return;
      }
      const text = await response.text();
      if (!looksUseful(url, text)) {
        return;
      }
      const file = path.join(
        responseDir,
        `${String(++responseIndex).padStart(4, "0")}_${safeName(url)}.txt`,
      );
      fs.writeFileSync(
        file,
        JSON.stringify(
          {
            url,
            status: response.status(),
            contentType,
            body: text,
          },
          null,
          2,
        ),
      );
    } catch {
      // Some streaming or cross-process responses cannot be read twice.
    }
  });

  console.log("Opening Feishu page. Please log in in the Chrome window if prompted.");
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60000 });

  const started = Date.now();
  let lastUrl = "";
  while (Date.now() - started < 5 * 60 * 1000) {
    await page.waitForTimeout(5000);
    const currentUrl = page.url();
    if (currentUrl !== lastUrl) {
      console.log("Current URL:", currentUrl);
      lastUrl = currentUrl;
    }
    const title = await page.title().catch(() => "");
    const bodyText = await page.locator("body").innerText({ timeout: 1000 }).catch(() => "");
    fs.writeFileSync(path.join(outDir, "page_text.txt"), bodyText);
    fs.writeFileSync(path.join(outDir, "page_title.txt"), title);
    fs.writeFileSync(path.join(outDir, "page_url.txt"), currentUrl);
    if (
      !/passport|login|accounts/i.test(currentUrl) &&
      /长音频|筛选|AA|http|音频|链接/.test(bodyText)
    ) {
      console.log("Page appears loaded; capturing network/data for 30 more seconds...");
      await page.waitForTimeout(30000);
      break;
    }
  }

  await page.screenshot({ path: path.join(outDir, "page.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(path.join(outDir, "page_html.html"), await page.content().catch(() => ""));
  console.log("Capture complete:", outDir);
  await context.close();
})();
