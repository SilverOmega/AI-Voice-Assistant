import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { SignIn } from "./components/SignIn";
import { useAuth } from "./hooks/useAuth";
import "./index.css";

// Install to the home screen, and notifications on Android. Needs https or localhost.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

/** Phones and other devices sign in first; the computer running the assistant goes straight in. */
function Root() {
  const { status, checked, refresh } = useAuth();
  if (!checked) return null;
  if (status && !status.signed_in) return <SignIn configured={status.configured} onSignedIn={refresh} />;
  return <App auth={status} onSignedOut={refresh} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
