// Test-only harness: starts the real platform API on an OS-assigned free
// port and prints it as `PORT=<n>` on the first stdout line, then keeps
// running until killed. Used by test_backend_worker_integration.py to spin
// up the actual server.ts (not a mock) for an end-to-end test.
import { createPlatformApi } from "../../platform/api/server.ts";

const api = createPlatformApi();
api.server.listen(0, () => {
  const address = api.server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  console.log(`PORT=${port}`);
});
