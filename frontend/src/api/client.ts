export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, "");

function getErrorMessage(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return undefined;
  }

  const { detail } = body;
  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      if (typeof entry === "object" && entry !== null && "msg" in entry && typeof entry.msg === "string") {
        return [entry.msg];
      }
      return [];
    });
    return messages.length > 0 ? messages.join(" ") : undefined;
  }

  return undefined;
}

export async function apiRequest<T>(
  path: string,
  accessToken: string,
  options: {
    method?: "GET" | "POST" | "PATCH";
    body?: unknown;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  if (!apiBaseUrl) {
    throw new ApiError("The API URL is not configured.");
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: options.method ?? "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("Could not reach Bonwise. Check your connection and try again.");
  }

  if (!response.ok) {
    let message: string | undefined;
    try {
      message = getErrorMessage(await response.json());
    } catch {
      message = undefined;
    }
    throw new ApiError(message ?? `The request failed (${response.status}).`, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Something went wrong. Please try again.";
}
