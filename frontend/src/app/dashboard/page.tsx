"use client";

import {
  useEffect,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import {
  apiFetch,
  getAccessToken,
} from "@/lib/api";
import {
  StatusBadge,
} from "@/components/ui";


type AttendanceRecord = {
  id: number;
  employee_id: string;
  employee_name: string;
  date: string;
  shift_name: string;
  late_minutes: number;
  early_departure_minutes: number;
  status: string;
};


type ExceptionData = {
  count: number;
  total_proposed_deduction: string;
};


export default function DashboardPage() {

  const router =
    useRouter();

  const [
    attendance,
    setAttendance,
  ] = useState<
    AttendanceRecord[]
  >([]);

  const [
    pending,
    setPending,
  ] = useState<ExceptionData>({
    count: 0,
    total_proposed_deduction: "0",
  });

  const [
    loading,
    setLoading,
  ] = useState(true);


  useEffect(() => {

    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    loadDashboard();

  }, [router]);


  async function loadDashboard() {

    try {

      const [
        attendanceResponse,
        exceptionResponse,
      ] = await Promise.all([

        apiFetch(
          "/attendance/today/"
        ),

        apiFetch(
          "/attendance/exceptions/pending/"
        ),
      ]);


      if (
        !attendanceResponse.ok ||
        !exceptionResponse.ok
      ) {
        throw new Error(
          "Unable to load dashboard."
        );
      }


      const attendanceData =
        await attendanceResponse.json();

      const exceptionData =
        await exceptionResponse.json();


      setAttendance(
        attendanceData.results || []
      );

      setPending({
        count:
          exceptionData.count || 0,

        total_proposed_deduction:
          exceptionData.total_proposed_deduction ||
          "0",
      });

    } catch (error) {

      console.error(error);

    } finally {

      setLoading(false);
    }
  }


  const present =
    attendance.filter(
      (item) =>
        item.status === "present"
    ).length;


  const late =
    attendance.filter(
      (item) =>
        item.status === "late"
    ).length;


  const absent =
    attendance.filter(
      (item) =>
        item.status === "absent"
    ).length;


  const incomplete =
    attendance.filter(
      (item) =>
        item.status === "incomplete"
    ).length;


  if (loading) {
    return (
      <div className="p-8">
        Loading dashboard...
      </div>
    );
  }


  return (
    <main className="p-8">

      <div className="flex items-center justify-between mb-8">

        <div>

          <h1 className="text-3xl font-bold text-slate-900">
            Dashboard
          </h1>

          <p className="text-slate-500 mt-1">
            Today's workforce overview
          </p>

        </div>

        <button
          onClick={loadDashboard}
          className="bg-white border border-slate-300 rounded-lg px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Refresh
        </button>

      </div>


      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-5 mb-8">

        <DashboardCard
          title="Attendance Records"
          value={attendance.length}
        />

        <DashboardCard
          title="Present"
          value={present}
        />

        <DashboardCard
          title="Late"
          value={late}
        />

        <DashboardCard
          title="Absent"
          value={absent}
        />

        <DashboardCard
          title="Pending Review"
          value={pending.count}
        />

      </div>


      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">

        <section className="xl:col-span-2 bg-white rounded-2xl shadow-sm border border-slate-200">

          <div className="p-6 border-b border-slate-200">

            <h2 className="font-bold text-lg text-slate-900">
              Today's Attendance
            </h2>

          </div>


          <div className="overflow-x-auto">

            <table className="w-full">

              <thead className="bg-slate-50">

                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">

                  <th className="px-6 py-4">
                    Employee
                  </th>

                  <th className="px-6 py-4">
                    Shift
                  </th>

                  <th className="px-6 py-4">
                    Late
                  </th>

                  <th className="px-6 py-4">
                    Status
                  </th>

                </tr>

              </thead>


              <tbody>

                {attendance.map(
                  (record) => (

                    <tr
                      key={record.id}
                      className="border-t border-slate-100"
                    >

                      <td className="px-6 py-4">

                        <div className="font-medium text-slate-900">
                          {record.employee_name}
                        </div>

                        <div className="text-sm text-slate-500">
                          {record.employee_id}
                        </div>

                      </td>


                      <td className="px-6 py-4 text-slate-700">
                        {record.shift_name}
                      </td>


                      <td className="px-6 py-4 text-slate-700">
                        {record.late_minutes} min
                      </td>


                      <td className="px-6 py-4">

                        <StatusBadge
                          status={record.status}
                        />

                      </td>

                    </tr>

                  )
                )}

              </tbody>

            </table>

          </div>

        </section>


        <section className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">

          <h2 className="font-bold text-lg text-slate-900">
            Attendance Exceptions
          </h2>

          <p className="text-sm text-slate-500 mt-1">
            Awaiting management decision
          </p>


          <div className="mt-8">

            <div className="text-5xl font-bold text-slate-900">
              {pending.count}
            </div>

            <div className="text-sm text-slate-500 mt-2">
              Pending cases
            </div>

          </div>


          <div className="mt-8 bg-amber-50 border border-amber-200 rounded-xl p-4">

            <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
              Proposed Deductions
            </div>

            <div className="text-2xl font-bold text-amber-900 mt-2">

              ₦
              {Number(
                pending.total_proposed_deduction
              ).toLocaleString()}

            </div>

            <div className="text-xs text-amber-700 mt-2">
              Not deducted until approved
            </div>

          </div>

        </section>

      </div>

    </main>
  );
}


function DashboardCard({
  title,
  value,
}: {
  title: string;
  value: number;
}) {

  return (
    <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">

      <div className="text-sm text-slate-500">
        {title}
      </div>

      <div className="text-3xl font-bold text-slate-900 mt-2">
        {value}
      </div>

    </div>
  );
}

