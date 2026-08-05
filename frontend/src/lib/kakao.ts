export function kakaoRedirectUri(): string {
  return `${window.location.origin}/auth/kakao/callback`;
}

export function kakaoAuthorizeUrl(): string {
  const clientId = process.env.NEXT_PUBLIC_KAKAO_CLIENT_ID;
  if (!clientId) {
    throw new Error("NEXT_PUBLIC_KAKAO_CLIENT_ID가 설정되지 않았습니다");
  }
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: kakaoRedirectUri(),
    response_type: "code",
  });
  return `https://kauth.kakao.com/oauth/authorize?${params.toString()}`;
}
