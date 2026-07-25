import Link from "next/link";

const menus = [
  { href: "/reviews", title: "리뷰 답글", desc: "쌓인 리뷰에 스타일 답글 달기" },
  { href: "/settlements", title: "정산 차액", desc: "주문 총액과 실입금액이 왜 다른지 분해" },
  { href: "/ads", title: "광고 순위 모니터링", desc: "CPC·순위·추천 액션 한눈에" },
];

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-bold">리뷰닥터 벤치마크 MVP</h1>
      <p className="mt-1 text-sm text-gray-500">배달매장 3대 현장 문제 — Mock 데이터 프로토타입</p>
      <div className="mt-6 grid gap-4">
        {menus.map((m) => (
          <Link key={m.href} href={m.href} className="rounded-lg border p-4 hover:bg-gray-50">
            <div className="font-semibold">{m.title}</div>
            <div className="text-sm text-gray-500">{m.desc}</div>
          </Link>
        ))}
      </div>
    </main>
  );
}
