// fork-private — render-time role gate
import { ReactNode } from 'react';
import { Role, usePermissionsStore } from '@/stores/fork-permissions-store';

interface RoleGateProps {
  /** Allowed roles. Defaults to ['admin']. */
  allow?: Role[];
  /** Rendered when the current user's role is NOT in `allow`. */
  fallback?: ReactNode;
  children: ReactNode;
}

/** Renders `children` only if the current user's role is in `allow`. */
export function RoleGate({ allow = ['admin'], fallback = null, children }: RoleGateProps) {
  const role = usePermissionsStore((s) => s.role);
  const loaded = usePermissionsStore((s) => s.loaded);

  if (!loaded) return null; // avoid flash; nav will repaint after /me resolves
  if (!role || !allow.includes(role)) return <>{fallback}</>;
  return <>{children}</>;
}
