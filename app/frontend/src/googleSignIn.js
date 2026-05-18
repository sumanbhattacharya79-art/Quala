/** Google Identity Services — sign-in button for AuthForm. */

export const GOOGLE_CLIENT_ID = String(import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "").trim();

export function isGoogleSignInConfigured() {
  return Boolean(GOOGLE_CLIENT_ID);
}

function waitForGoogleIdentity(timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    if (typeof window !== "undefined" && window.google?.accounts?.id) {
      resolve(window.google.accounts);
      return;
    }
    const start = Date.now();
    const tick = () => {
      if (window.google?.accounts?.id) {
        resolve(window.google.accounts);
        return;
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error("Google sign-in failed to load"));
        return;
      }
      window.setTimeout(tick, 80);
    };
    tick();
  });
}

/**
 * Mount the official Google button into containerEl.
 * onCredential(response) receives GIS CredentialResponse with .credential (JWT).
 */
export async function mountGoogleSignInButton(containerEl, onCredential) {
  if (!containerEl || !GOOGLE_CLIENT_ID) return () => {};
  const accounts = await waitForGoogleIdentity();
  accounts.id.initialize({
    client_id: GOOGLE_CLIENT_ID,
    callback: onCredential,
    auto_select: false,
    cancel_on_tap_outside: true,
  });
  const width = Math.min(400, Math.max(200, containerEl.clientWidth || 280));
  accounts.id.renderButton(containerEl, {
    type: "standard",
    theme: "outline",
    size: "large",
    text: "continue_with",
    shape: "rectangular",
    width,
  });
  return () => {
    try {
      containerEl.innerHTML = "";
    } catch {
      /* ignore */
    }
  };
}
