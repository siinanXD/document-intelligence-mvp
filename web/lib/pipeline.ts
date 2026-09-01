export const STAGE_LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  parsed: "Parsed",
  chunked: "Chunked",
  embedded: "Embedded",
  indexed: "Indexed",
  ready: "Ready",
};

export function stageLabel(id: string): string {
  return STAGE_LABELS[id] ?? id;
}
