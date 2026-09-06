import Sidebar
  from "@/components/Sidebar";


export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {

  return (
    <div className="min-h-screen bg-slate-100">

      <Sidebar />

      <div className="ml-64">
        {children}
      </div>

    </div>
  );
}