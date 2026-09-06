
const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000/api";


export type CurrentUser = {
  id: number;
  username: string;
  is_superuser: boolean;
  permissions: {
    record_meal_operations: boolean;
    review_meal_excess: boolean;
    manage_meal_configuration: boolean;
  };
};


export function getAccessToken() {
  if (typeof window === "undefined") {
    return null;
  }

  return localStorage.getItem(
    "rotic_access_token"
  );
}


export function getRefreshToken() {
  if (typeof window === "undefined") {
    return null;
  }

  return localStorage.getItem(
    "rotic_refresh_token"
  );
}


export function saveTokens(
  access: string,
  refresh: string
) {
  localStorage.setItem(
    "rotic_access_token",
    access
  );

  localStorage.setItem(
    "rotic_refresh_token",
    refresh
  );
}


export function clearTokens() {
  localStorage.removeItem(
    "rotic_access_token"
  );

  localStorage.removeItem(
    "rotic_refresh_token"
  );
}


export async function login(
  username: string,
  password: string
) {
  const response = await fetch(
    `${API_BASE_URL}/auth/login/`,
    {
      method: "POST",

      headers: {
        "Content-Type":
          "application/json",
      },

      body: JSON.stringify({
        username,
        password,
      }),
    }
  );


  if (!response.ok) {
    throw new Error(
      "Invalid username or password."
    );
  }


  const data =
    await response.json();


  saveTokens(
    data.access,
    data.refresh
  );


  return data;
}


export async function apiFetch(
  path: string,
  options: RequestInit = {}
) {
  const token =
    getAccessToken();


  const headers =
    new Headers(
      options.headers || {}
    );


  /*
   * IMPORTANT:
   *
   * JSON requests need:
   * Content-Type: application/json
   *
   * FormData uploads must NOT have
   * Content-Type manually specified.
   *
   * The browser will automatically create:
   *
   * multipart/form-data;
   * boundary=...
   *
   * This is required for Excel/CSV uploads.
   */
  const isFormData =
    typeof FormData !== "undefined" &&
    options.body instanceof FormData;


  if (isFormData) {
    headers.delete(
      "Content-Type"
    );
  } else if (
    options.body !== undefined &&
    options.body !== null &&
    !headers.has(
      "Content-Type"
    )
  ) {
    headers.set(
      "Content-Type",
      "application/json"
    );
  }


  if (token) {
    headers.set(
      "Authorization",
      `Bearer ${token}`
    );
  }


  let response =
    await fetch(
      `${API_BASE_URL}${path}`,
      {
        ...options,
        headers,
      }
    );


  /*
   * If access token expired,
   * attempt JWT refresh.
   */
  if (response.status === 401) {
    const refreshToken =
      getRefreshToken();


    if (!refreshToken) {
      clearTokens();

      throw new Error(
        "Authentication required."
      );
    }


    const refreshResponse =
      await fetch(
        `${API_BASE_URL}/auth/refresh/`,
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify({
            refresh:
              refreshToken,
          }),
        }
      );


    if (!refreshResponse.ok) {
      clearTokens();

      throw new Error(
        "Your session has expired."
      );
    }


    const refreshData =
      await refreshResponse.json();


    localStorage.setItem(
      "rotic_access_token",
      refreshData.access
    );


    headers.set(
      "Authorization",
      `Bearer ${refreshData.access}`
    );


    /*
     * Keep the same Content-Type rules
     * when retrying the original request.
     */
    if (isFormData) {
      headers.delete(
        "Content-Type"
      );
    }


    response =
      await fetch(
        `${API_BASE_URL}${path}`,
        {
          ...options,
          headers,
        }
      );
  }


  return response;
}


export async function logout() {
  const refreshToken = getRefreshToken();

  if (refreshToken) {
    try {
      /*
       * Best-effort: blacklist the refresh token server-side so it can't be
       * replayed even if it leaked. Local tokens are cleared either way.
       */
      await apiFetch("/auth/logout/", {
        method: "POST",
        body: JSON.stringify({ refresh: refreshToken }),
      });
    } catch {
      // Ignore network errors here - the user is signing out regardless.
    }
  }

  clearTokens();
}


export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch("/auth/me/");

  if (!response.ok) {
    throw new Error("Unable to load the current user.");
  }

  return response.json();
}
