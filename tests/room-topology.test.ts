import assert from "node:assert/strict";
import test from "node:test";
import { roomMediaPlan, shouldInitiatePeer } from "../app/room-topology.ts";

test("every participant pair chooses exactly one WebRTC offerer", () => {
  for (const [first, second] of [["alpha", "beta"], ["d91", "1ac"], ["host-z", "audience-a"]]) {
    assert.notEqual(shouldInitiatePeer(first, second), shouldInitiatePeer(second, first));
  }
});

test("all roles publish camera while audio stays on the correct concert paths", () => {
  const performerToHost = roomMediaPlan("performer", "host");
  const hostToAudience = roomMediaPlan("host", "audience");
  const performerToPerformer = roomMediaPlan("performer", "performer");
  const audienceToHost = roomMediaPlan("audience", "host");

  assert.equal(performerToHost.sendCamera, true);
  assert.equal(performerToHost.sendPerformerStem, true);
  assert.equal(hostToAudience.sendCamera, true);
  assert.equal(hostToAudience.sendAudienceProgram, true);
  assert.equal(performerToPerformer.sendCamera, true);
  assert.equal(performerToPerformer.sendPerformerStem, false);
  assert.equal(audienceToHost.sendCamera, true);
  assert.equal(audienceToHost.sendAudienceProgram, false);
});

test("the host receives performer stems and the audience receives only the mastered return", () => {
  assert.equal(roomMediaPlan("host", "performer").receivePerformerStem, true);
  assert.equal(roomMediaPlan("audience", "host").receiveAudienceProgram, true);
  assert.equal(roomMediaPlan("performer", "performer").receiveAudienceProgram, false);
});
