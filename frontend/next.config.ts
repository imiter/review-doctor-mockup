import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 로컬 개발 중 같은 와이파이의 폰/다른 기기에서 접속해 반응형을 확인할 때만
  // 필요한 설정 — 없으면 Next dev 서버가 HMR 웹소켓을 cross-origin으로 보고
  // 막아버려 페이지가 하이드레이션되지 않고(=버튼이 눌리지 않고) 조용히 깨진다.
  // 프로덕션 빌드(Railway)에는 영향 없음(dev 서버 전용 설정).
  allowedDevOrigins: ["192.168.219.102"],
  images: {
    // 리뷰에 첨부된 고객 사진(배민 CDN)만 next/image 최적화 대상으로 허용한다.
    remotePatterns: [new URL("https://bmreview.cdn.baemin.com/**")],
  },
};

export default nextConfig;
