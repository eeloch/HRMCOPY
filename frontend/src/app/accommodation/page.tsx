"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Accommodation lives on the Employees page now; keep old links working.
export default function AccommodationRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/employees?tab=accommodation"); }, [router]);
  return null;
}
