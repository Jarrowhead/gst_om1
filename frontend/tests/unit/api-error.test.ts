import { describe, it, expect } from "vitest";
import { ApiError } from "@/lib/api/client";

describe("ApiError Class", () => {
  it("initializes properties properly", () => {
    const error = new ApiError("GSTIN_INVALID", "The provided GSTIN is invalid", 422);

    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.code).toBe("GSTIN_INVALID");
    expect(error.message).toBe("The provided GSTIN is invalid");
    expect(error.status).toBe(422);
  });

  it("handles 401 unauthorized errors", () => {
    const error = new ApiError("UNAUTHORIZED", "Session expired", 401);
    expect(error.status).toBe(401);
    expect(error.code).toBe("UNAUTHORIZED");
  });

  it("handles 404 not found errors", () => {
    const error = new ApiError("NOT_FOUND", "Document not found", 404);
    expect(error.status).toBe(404);
  });
});
