if (new URLSearchParams(window.location.search).get("run") === "1") {
  const results = [];

  function record(name, passed) {
    results.push(`${passed ? "PASS" : "FAIL"}: ${name}`);
    return passed;
  }

  function assert(name, condition) {
    if (!record(name, condition)) throw new Error(name);
  }

  async function waitFor(description, condition, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (condition()) return;
      await new Promise((resolve) => window.setTimeout(resolve, 30));
    }
    throw new Error(`Timed out waiting for ${description}.`);
  }

  async function run() {
    try {
      const dialog = document.querySelector(".pdf-viewer-dialog");
      const pageInput = dialog.querySelector(".pdf-viewer-page-input");
      const textLayer = dialog.querySelector(".pdf-viewer-text-layer");

      document.querySelector('a[href*="synthetic-viewer-fixture.pdf#page=2"]').click();
      await waitFor("the cited PDF to render on physical page 2", () =>
        dialog.open && pageInput.value === "2" && textLayer.querySelectorAll(".pdf-quote-highlight").length > 0,
      );
      assert("citation click opens the viewer at physical #page=2", pageInput.value === "2");
      assert("the verbatim quote is displayed", dialog.querySelector(".pdf-viewer-quote").textContent.includes("Expenses totaled 37 million synthetic units."));
      assert("the matching quote is highlighted in the PDF text layer", textLayer.querySelectorAll(".pdf-quote-highlight").length > 0);

      dialog.querySelector(".pdf-viewer-previous").click();
      await waitFor("previous-page navigation", () => pageInput.value === "1");
      assert("previous button navigates to physical page 1", pageInput.value === "1");

      dialog.querySelector(".pdf-viewer-next").click();
      await waitFor("next-page navigation", () => pageInput.value === "2");
      assert("next button navigates to physical page 2", pageInput.value === "2");

      document.querySelector('a[href*="synthetic-viewer-fixture.pdf#page=99"]').click();
      await waitFor("out-of-range page warning", () =>
        dialog.querySelector(".pdf-viewer-empty")?.textContent.includes("outside this PDF") === true,
      );
      assert("out-of-range physical page is reported and clamped", pageInput.value === "1");

      document.querySelector('a[href*="synthetic-second-viewer-fixture.pdf"]').click();
      await waitFor("the second PDF to render", () =>
        dialog.querySelector(".pdf-viewer-title").textContent.includes("synthetic-second-viewer-fixture.pdf")
        && textLayer.querySelectorAll(".pdf-quote-highlight").length > 0,
      );
      assert("citation switches to a different PDF and highlights its quote", textLayer.querySelectorAll(".pdf-quote-highlight").length > 0);

      document.querySelector('a[href*="missing-synthetic-fixture.pdf"]').click();
      await waitFor("the PDF loading error message", () =>
        dialog.querySelector(".pdf-viewer-error")?.textContent.includes("Could not load") === true,
      );
      assert("missing PDF displays a loading error", true);
    } catch (error) {
      record(error instanceof Error ? error.message : "Unexpected test error", false);
    }

    const output = document.createElement("pre");
    output.id = "viewer-test-results";
    output.dataset.status = results.every((result) => result.startsWith("PASS:")) ? "passed" : "failed";
    output.textContent = results.join("\n");
    document.body.append(output);
  }

  run();
}
