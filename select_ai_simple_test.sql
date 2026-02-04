-- =============================================================================
-- Select AI 간단 테스트: 프로필 생성 + 채팅
-- (Oracle 문서 기준: DBMS_CLOUD_AI + DBMS_CLOUD credential 사용)
-- https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/select-ai-examples.html
-- =============================================================================

-- 0) 사전: 관리자가 한 번 실행
--    GRANT EXECUTE ON DBMS_CLOUD_AI TO ADMIN;
--    (OCI GenAI는 Network ACL 불필요)

-- 1) OCI GenAI용 credential (Select AI는 DBMS_CLOUD.CREATE_CREDENTIAL 사용)
--    아래 값은 본인 OCI 콘솔에서 채우기.
BEGIN
  DBMS_CLOUD.CREATE_CREDENTIAL(
    credential_name => 'GENAI_CRED',
    user_ocid       => 'ocid1.user.oc1..<your-user-ocid>',
    tenancy_ocid    => 'ocid1.tenancy.oc1..<your-tenancy-ocid>',
    private_key    => '<private-key-content-without-BEGIN-END-lines>',
    fingerprint    => '<your-fingerprint>'
  );
END;
/

-- 2) AI 프로필 생성 (object_list는 빈 배열 또는 본인 스키마 테이블로 변경)
BEGIN
  DBMS_CLOUD_AI.CREATE_PROFILE(
    profile_name => 'GENAI',
    attributes   => '{"provider": "oci",
      "credential_name": "GENAI_CRED",
      "object_list": [{"owner": "ADMIN"}]
    }'
  );
END;
/

-- 3) 현재 세션에 프로필 설정
EXEC DBMS_CLOUD_AI.SET_PROFILE('GENAI');

-- 4) 프로필 확인
SELECT DBMS_CLOUD_AI.get_profile() FROM dual;

-- 5) 간단 채팅 테스트 (DB/LLM 연결 확인)
SELECT AI chat what is Oracle Autonomous Database in one short sentence;

-- 6) (선택) 프로필 해제
-- BEGIN DBMS_CLOUD_AI.CLEAR_PROFILE; END; /

-- 7) (선택) 프로필 삭제
-- EXEC DBMS_CLOUD_AI.DROP_PROFILE('GENAI');
