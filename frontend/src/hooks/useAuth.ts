import { useCallback, useEffect, useState } from "react";
import { AUTH_REQUIRED_EVENT, auth } from "../lib/api";
import type { AuthStatus } from "../lib/types";

/**
 * Whether this device may use the app. The computer running the assistant always may; phones
 * and other devices sign in once with the passcode. `status` is null while checking, and also
 * when the server can't be reached: the app then opens and its health indicators say why.
 */
export function useAuth() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [checked, setChecked] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await auth.status());
    } catch {
      setStatus(null);
    } finally {
      setChecked(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // A request was refused: signed out from elsewhere, or the passcode changed.
    const onRequired = () => void refresh();
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
  }, [refresh]);

  return { status, checked, refresh };
}
