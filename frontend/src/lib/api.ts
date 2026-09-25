
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
    view_salary: boolean;
    manage_devices: boolean;
    manage_shifts: boolean;
    manage_roster: boolean;
    review_attendanceexception: boolean;
    review_overtime: boolean;
    approve_leave: boolean;
    manage_leave_policy: boolean;
    view_payroll: boolean;
    manage_payroll: boolean;
    record_ppe_issue: boolean;
    review_ppe_deduction: boolean;
    view_documents: boolean;
    manage_documents: boolean;
    record_employee_offences: boolean;
    review_employee_offences: boolean;
    manage_offence_configuration: boolean;
    view_bank_details: boolean;
    record_salary_advance: boolean;
    approve_salary_advance: boolean;
    pay_salary_advance: boolean;
    view_deferred_funds: boolean;
    manage_deferred_funds: boolean;
    approve_deferred_withdrawal: boolean;
    pay_deferred_withdrawal: boolean;
    view_accommodation: boolean;
    manage_accommodation: boolean;
    view_bonuses: boolean;
    record_bonus: boolean;
    approve_bonus: boolean;
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
  localStorage.setItem("rotic_session_id", crypto.randomUUID());
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
  localStorage.removeItem("rotic_session_id");
  localStorage.removeItem(
    "rotic_access_token"
  );

  localStorage.removeItem(
    "rotic_refresh_token"
  );
}

let refreshInFlight: { token: string; promise: Promise<string> } | null = null;

async function refreshAccessToken(refreshToken: string): Promise<string> {
  if (!refreshInFlight || refreshInFlight.token !== refreshToken) {
    const sessionId = localStorage.getItem("rotic_session_id");
    const attempt = (async () => {
      const rotate = async () => {
        if (localStorage.getItem("rotic_session_id") !== sessionId) throw new Error("Your session has changed.");
        if (getRefreshToken() !== refreshToken) {
          const currentAccess = getAccessToken();
          if (currentAccess) return currentAccess;
          throw new Error("Your session has expired.");
        }
        const response = await fetch(`${API_BASE_URL}/auth/refresh/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh: refreshToken }),
        });
        if (!response.ok) throw new Error("Your session has expired.");
        const data = await response.json();
        if (!data.access || !data.refresh) throw new Error("Your session has expired.");
        if (localStorage.getItem("rotic_session_id") !== sessionId || getRefreshToken() !== refreshToken) {
          throw new Error("Your session has changed.");
        }
        localStorage.setItem("rotic_access_token", data.access);
        localStorage.setItem("rotic_refresh_token", data.refresh);
        return data.access as string;
      };
      if (typeof navigator !== "undefined" && navigator.locks) {
        return navigator.locks.request("rotic-refresh-token", rotate);
      }
      return rotate();
    })();
    refreshInFlight = { token: refreshToken, promise: attempt };
    void attempt.finally(() => {
      if (refreshInFlight?.promise === attempt) refreshInFlight = null;
    }).catch(() => {});
  }
  return refreshInFlight.promise;
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
  const sessionId = localStorage.getItem("rotic_session_id");


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
    if (localStorage.getItem("rotic_session_id") !== sessionId) throw new Error("Your session has changed.");
    const currentAccess = getAccessToken();
    let access: string;
    if (currentAccess && currentAccess !== token) {
      access = currentAccess;
    } else {
      const refreshToken = getRefreshToken();
      if (!refreshToken) throw new Error("Authentication required.");
      try {
        access = await refreshAccessToken(refreshToken);
      } catch (error) {
        if (localStorage.getItem("rotic_session_id") === sessionId && getRefreshToken() === refreshToken) clearTokens();
        throw error;
      }
    }
    if (localStorage.getItem("rotic_session_id") !== sessionId) throw new Error("Your session has changed.");


    headers.set(
      "Authorization",
      `Bearer ${access}`
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
