document.addEventListener("DOMContentLoaded", () => {
  const codeEl = document.getElementById("recovery");
  const copyBtn = document.getElementById("copy");
  const downloadBtn = document.getElementById("download");
  const qrBtn = document.getElementById("qr-toggle");
  const qrCanvas = document.getElementById("qr-code");

  const code = codeEl?.textContent.trim();

  copyBtn?.addEventListener("click", async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      const original = copyBtn.textContent;
      copyBtn.textContent = "Copied";
      setTimeout(() => (copyBtn.textContent = original), 1500);
    } catch (e) {
      console.error("copy failed", e);
    }
  });

  downloadBtn?.addEventListener("click", () => {
    if (!code) return;
    const blob = new Blob([code + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "recovery-code.txt";
    a.click();
    URL.revokeObjectURL(url);
  });

});
