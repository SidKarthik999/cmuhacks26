export type RoomMediaRole = "host" | "performer" | "audience";

export type RoomMediaPlan = {
  sendCamera: boolean;
  sendPerformerStem: boolean;
  sendAudienceProgram: boolean;
  receivePerformerStem: boolean;
  receiveAudienceProgram: boolean;
};

export function shouldInitiatePeer(localId: string, remoteId: string) {
  return localId.localeCompare(remoteId) < 0;
}

export function roomMediaPlan(localRole: RoomMediaRole, remoteRole: RoomMediaRole): RoomMediaPlan {
  return {
    sendCamera: true,
    sendPerformerStem: localRole === "performer" && remoteRole === "host",
    sendAudienceProgram: localRole === "host" && remoteRole === "audience",
    receivePerformerStem: localRole === "host" && remoteRole === "performer",
    receiveAudienceProgram: localRole === "audience" && remoteRole === "host",
  };
}
