import { describe, it, expect } from "vitest";
import {
  gstinFormatValid,
  panFormatValid,
  gstinChecksumValid,
  gstinPanMatches,
} from "@/lib/validation/gstin";

describe("GSTIN & PAN Client-Side Validation", () => {
  describe("gstinFormatValid", () => {
    it("accepts structurally valid 15-character GSTINs", () => {
      expect(gstinFormatValid("27AAPFU0939F1ZV")).toBe(true);
      expect(gstinFormatValid("29AABCU9603R1ZJ")).toBe(true);
      expect(gstinFormatValid("07AAAAA0000A1Z5")).toBe(true);
    });

    it("handles lowercase and whitespace trimming", () => {
      expect(gstinFormatValid("  27aapfu0939f1zv  ")).toBe(true);
    });

    it("rejects invalid lengths", () => {
      expect(gstinFormatValid("27AAPFU0939F1Z")).toBe(false); // 14 chars
      expect(gstinFormatValid("27AAPFU0939F1ZVV")).toBe(false); // 16 chars
      expect(gstinFormatValid("")).toBe(false);
    });

    it("rejects invalid character classes", () => {
      expect(gstinFormatValid("XXAAPFU0939F1ZV")).toBe(false); // non-numeric state code
      expect(gstinFormatValid("27123450939F1ZV")).toBe(false); // numeric PAN entity letters
      expect(gstinFormatValid("27AAPFU0939F1AV")).toBe(false); // 14th char not Z
    });
  });

  describe("panFormatValid", () => {
    it("accepts valid 10-character PANs", () => {
      expect(panFormatValid("AAPFU0939F")).toBe(true);
      expect(panFormatValid("AABCU9603R")).toBe(true);
      expect(panFormatValid("abcde1234f")).toBe(true); // lower case
    });

    it("rejects invalid PAN patterns", () => {
      expect(panFormatValid("1APFU0939F")).toBe(false);
      expect(panFormatValid("AAPFU0939")).toBe(false); // 9 chars
      expect(panFormatValid("AAPFU0939FF")).toBe(false); // 11 chars
      expect(panFormatValid("AAPF00939F")).toBe(false); // digit in letters part
    });
  });

  describe("gstinChecksumValid (Modulo 36 Luhn Variant)", () => {
    it("validates authentic GSTIN checksums", () => {
      expect(gstinChecksumValid("27AAPFU0939F1ZV")).toBe(true);
      expect(gstinChecksumValid("29AABCU9603R1ZJ")).toBe(true);
      expect(gstinChecksumValid("07AAACR5055K1Z9")).toBe(true);
      expect(gstinChecksumValid("33AAGCP1234M1ZH")).toBe(true);
    });

    it("detects single-character tampering and typos", () => {
      // Last check digit tampered
      expect(gstinChecksumValid("27AAPFU0939F1ZZ")).toBe(false);
      expect(gstinChecksumValid("27AAPFU0939F1ZA")).toBe(false);
      // State digit tampered
      expect(gstinChecksumValid("28AAPFU0939F1ZV")).toBe(false);
      // PAN digit tampered
      expect(gstinChecksumValid("27AAPFU0938F1ZV")).toBe(false);
    });
  });

  describe("gstinPanMatches", () => {
    it("correctly checks embedded PAN against standalone PAN", () => {
      expect(gstinPanMatches("27AAPFU0939F1ZV", "AAPFU0939F")).toBe(true);
      expect(gstinPanMatches("29AABCU9603R1ZJ", "AABCU9603R")).toBe(true);
      expect(gstinPanMatches("27AAPFU0939F1ZV", "AABCU9603R")).toBe(false);
      expect(gstinPanMatches("27aapfu0939f1zv", "aapfu0939f")).toBe(true);
    });
  });
});
