"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import {
  login,
} from "@/lib/api";


export default function LoginPage() {

  const router = useRouter();

  const [
    username,
    setUsername,
  ] = useState("");

  const [
    password,
    setPassword,
  ] = useState("");

  const [
    error,
    setError,
  ] = useState("");

  const [
    loading,
    setLoading,
  ] = useState(false);


  async function handleSubmit(
    event: FormEvent
  ) {
    event.preventDefault();

    setError("");
    setLoading(true);

    try {
      await login(
        username,
        password
      );

      router.push("/dashboard");

    } catch (err) {

      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError(
          "Unable to log in."
        );
      }

    } finally {
      setLoading(false);
    }
  }


  return (
    <main className="min-h-screen bg-slate-100 flex items-center justify-center p-6">

      <div className="w-full max-w-md bg-white rounded-2xl shadow-lg p-8">

        <div className="mb-8">

          <div className="text-sm font-semibold text-blue-600 mb-2">
            ROTIC ALUMINIUM
          </div>

          <h1 className="text-3xl font-bold text-slate-900">
            HRM System
          </h1>

          <p className="text-slate-500 mt-2">
            Sign in to manage employees,
            attendance and payroll.
          </p>

        </div>


        <form
          onSubmit={handleSubmit}
          className="space-y-5"
        >

          <div>

            <label className="block text-sm font-medium text-slate-700 mb-2">
              Username
            </label>

            <input
              value={username}
              onChange={(e) =>
                setUsername(
                  e.target.value
                )
              }
              className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
              required
            />

          </div>


          <div>

            <label className="block text-sm font-medium text-slate-700 mb-2">
              Password
            </label>

            <input
              type="password"
              value={password}
              onChange={(e) =>
                setPassword(
                  e.target.value
                )
              }
              className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
              required
            />

          </div>


          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl px-4 py-3 text-sm">
              {error}
            </div>
          )}


          <button
            disabled={loading}
            type="submit"
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-semibold rounded-xl py-3 transition"
          >
            {loading
              ? "Signing in..."
              : "Sign In"}
          </button>

        </form>

      </div>

    </main>
  );
}