import { describe, it, expect } from "vitest";
import {
  isTerminalStatus,
  isDraftReady,
  stageState,
  TERMINAL_STATUSES,
  PIPELINE_STAGES,
} from "@/lib/extraction/status";

describe("Extraction Pipeline Status Utilities", () => {
  describe("isTerminalStatus", () => {
    it("identifies all terminal status outcomes correctly", () => {
      for (const status of TERMINAL_STATUSES) {
        expect(isTerminalStatus(status)).toBe(true);
      }
    });

    it("identifies in-flight and non-terminal statuses as false", () => {
      expect(isTerminalStatus("QUEUED")).toBe(false);
      expect(isTerminalStatus("PREPROCESS")).toBe(false);
      expect(isTerminalStatus("OCR_RUNNING")).toBe(false);
      expect(isTerminalStatus("LLM_RUNNING")).toBe(false);
      expect(isTerminalStatus("")).toBe(false);
      expect(isTerminalStatus(null)).toBe(false);
      expect(isTerminalStatus(undefined)).toBe(false);
    });
  });

  describe("isDraftReady", () => {
    it("allows review and confirm for valid draft states", () => {
      expect(isDraftReady("EXTRACTED")).toBe(true);
      expect(isDraftReady("NEEDS_REVIEW")).toBe(true);
      expect(isDraftReady("CONFIRMED")).toBe(true);
    });

    it("gates review and confirm for unfinished or failed states", () => {
      expect(isDraftReady("QUEUED")).toBe(false);
      expect(isDraftReady("PREPROCESS")).toBe(false);
      expect(isDraftReady("OCR_RUNNING")).toBe(false);
      expect(isDraftReady("LLM_RUNNING")).toBe(false);
      expect(isDraftReady("FAILED")).toBe(false);
      expect(isDraftReady(null)).toBe(false);
    });
  });

  describe("stageState", () => {
    it("marks all stages done when a non-FAILED terminal outcome is reached", () => {
      for (const stage of PIPELINE_STAGES) {
        expect(stageState(stage, "CONFIRMED")).toBe("done");
        expect(stageState(stage, "EXTRACTED")).toBe("done");
        expect(stageState(stage, "NEEDS_REVIEW")).toBe("done");
      }
    });

    it("correctly indicates active stage when OCR is running", () => {
      expect(stageState("QUEUED", "OCR_RUNNING")).toBe("done");
      expect(stageState("PREPROCESS", "OCR_RUNNING")).toBe("done");
      expect(stageState("OCR_RUNNING", "OCR_RUNNING")).toBe("active");
      expect(stageState("LLM_RUNNING", "OCR_RUNNING")).toBe("pending");
      expect(stageState("EXTRACTED", "OCR_RUNNING")).toBe("pending");
    });

    it("marks pending when status is FAILED or unknown", () => {
      expect(stageState("OCR_RUNNING", "FAILED")).toBe("pending");
      expect(stageState("QUEUED", "UNKNOWN_STATUS")).toBe("pending");
    });
  });
});
