import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    // 리뷰에 첨부된 고객 사진(배민 CDN)만 next/image 최적화 대상으로 허용한다.
    remotePatterns: [new URL("https://bmreview.cdn.baemin.com/**")],
  },
};

export default nextConfig;
