// Device fingerprint for the anti-spam 3-strike rule. Not a hardware ID: identical devices/browsers can collide, and a
// user can change it (other browser, cleared settings). The server therefore also keeps an IP-based ban as a fallback.
export async function getDeviceFingerprint() {
  const data = [
    navigator.userAgent,
    navigator.language,
    screen.width + "x" + screen.height,
    screen.colorDepth,
    new Date().getTimezoneOffset(),
    navigator.hardwareConcurrency || 0,
    navigator.deviceMemory || 0,
  ];

  // Canvas fingerprinting (renders a hidden graphic unique to the GPU)
  try {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    ctx.textBaseline = "top";
    ctx.font = "14px Arial";
    ctx.fillStyle = "#f60";
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = "#069";
    ctx.fillText("PRALAY-AI", 2, 15);
    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
    ctx.fillText("Device-ID", 4, 17);
    data.push(canvas.toDataURL());
  } catch (e) {
    data.push("no-canvas");
  }

  const raw = data.join("|");

  // Simple hash function
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    const char = raw.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return "fp_" + Math.abs(hash).toString(16);
}
