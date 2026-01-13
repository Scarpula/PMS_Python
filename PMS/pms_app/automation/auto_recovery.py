"""
자동 복구 시퀀스 관리자
PMS 재시작 시 BMS Communication 에러 자동 복구
"""

import asyncio
import logging
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..devices.bms_handler import BMSHandler
    from ..devices.pcs_handler import PCSHandler


class AutoRecoveryManager:
    """자동 복구 시퀀스 관리자"""

    # BMS Error Code 2의 Communication Error 비트 (b3)
    BMS_COMMUNICATION_ERROR_BIT = 3
    BMS_COMMUNICATION_ERROR_VALUE = 1 << BMS_COMMUNICATION_ERROR_BIT  # 0x0008 = 8

    def __init__(self, bms_handler: 'BMSHandler', pcs_handler: 'PCSHandler'):
        """
        자동 복구 관리자 초기화

        Args:
            bms_handler: BMS 핸들러
            pcs_handler: PCS 핸들러
        """
        self.bms_handler = bms_handler
        self.pcs_handler = pcs_handler
        self.logger = logging.getLogger(self.__class__.__name__)

        self.recovery_in_progress = False
        self.last_recovery_attempt = None
        self.recovery_count = 0

        self.logger.info("🔧 자동 복구 관리자 초기화 완료")

    def _check_communication_error(self, error_code_2: int) -> bool:
        """
        BMS Error Code 2에서 Communication Error 확인

        Args:
            error_code_2: Error Code 2 레지스터 값 (Decimal)

        Returns:
            Communication Error 발생 여부
        """
        has_error = bool(error_code_2 & self.BMS_COMMUNICATION_ERROR_VALUE)

        if has_error:
            self.logger.warning(
                f"⚠️ BMS Communication Error 감지: "
                f"Error Code 2 = {error_code_2} (0x{error_code_2:04X}, "
                f"Binary: {bin(error_code_2)[2:].zfill(16)}), "
                f"Bit {self.BMS_COMMUNICATION_ERROR_BIT} = 1"
            )

        return has_error

    async def check_and_recover(self, bms_data: Optional[Dict[str, Any]]) -> bool:
        """
        BMS 데이터에서 Communication Error 확인 및 자동 복구 수행

        Args:
            bms_data: BMS 원시 데이터 (read_data 결과)

        Returns:
            복구 시도 여부 (True: 복구 시도함, False: 복구 불필요 또는 이미 진행 중)
        """
        # 복구 중이면 중복 실행 방지
        if self.recovery_in_progress:
            self.logger.debug("이미 복구 작업이 진행 중입니다")
            return False

        # BMS 데이터가 없으면 복구 불필요
        if not bms_data:
            return False

        # Error Code 2 확인
        error_code_2_raw = bms_data.get('error_code_2')
        if error_code_2_raw is None:
            self.logger.debug("Error Code 2 데이터가 없습니다")
            return False

        # Communication Error 확인
        if not self._check_communication_error(error_code_2_raw):
            return False

        # 자동 복구 시퀀스 실행
        self.logger.info("🔄 BMS Communication Error 감지 - 자동 복구 시퀀스 시작")
        self.recovery_in_progress = True

        try:
            success = await self._execute_recovery_sequence()

            if success:
                self.recovery_count += 1
                self.logger.info(f"✅ 자동 복구 시퀀스 완료 (총 {self.recovery_count}회)")
            else:
                self.logger.error("❌ 자동 복구 시퀀스 실패")

            return success

        except Exception as e:
            self.logger.error(f"❌ 자동 복구 시퀀스 중 예외 발생: {e}", exc_info=True)
            return False
        finally:
            self.recovery_in_progress = False

    async def _execute_recovery_sequence(self) -> bool:
        """
        자동 복구 시퀀스 실행

        순서:
        1. BMS 에러 리셋
        2. 대기 (2초)
        3. BMS DC 컨택터 ON
        4. 대기 (3초)
        5. PCS 리셋
        6. 대기 (2초)
        7. PCS 독립운전 모드 실행

        Returns:
            전체 시퀀스 성공 여부
        """
        self.logger.info("=" * 60)
        self.logger.info("🔧 자동 복구 시퀀스 시작")
        self.logger.info("=" * 60)

        try:
            # 1. BMS 에러 리셋
            self.logger.info("1️⃣ BMS 에러 리셋 실행")
            bms_reset_success = await self.bms_handler.reset_errors()

            if not bms_reset_success:
                self.logger.error("❌ BMS 에러 리셋 실패")
                return False

            self.logger.info("✅ BMS 에러 리셋 성공")
            await asyncio.sleep(2.0)  # 2초 대기

            # 2. BMS DC 컨택터 ON
            self.logger.info("2️⃣ BMS DC 컨택터 ON 실행")
            dc_contactor_success = await self.bms_handler.control_dc_contactor(True)

            if not dc_contactor_success:
                self.logger.error("❌ BMS DC 컨택터 ON 실패")
                return False

            self.logger.info("✅ BMS DC 컨택터 ON 성공")
            await asyncio.sleep(3.0)  # 3초 대기

            # 3. PCS 리셋
            self.logger.info("3️⃣ PCS 리셋 실행")
            pcs_reset_success = await self.pcs_handler.reset_faults()

            if not pcs_reset_success:
                self.logger.error("❌ PCS 리셋 실패")
                return False

            self.logger.info("✅ PCS 리셋 성공")
            await asyncio.sleep(2.0)  # 2초 대기

            # 4. PCS 독립운전 모드 실행
            self.logger.info("4️⃣ PCS 독립운전 모드 실행")
            independent_mode_success = await self.pcs_handler.set_operation_mode('independent')

            if not independent_mode_success:
                self.logger.error("❌ PCS 독립운전 모드 실행 실패")
                return False

            self.logger.info("✅ PCS 독립운전 모드 실행 성공")

            self.logger.info("=" * 60)
            self.logger.info("✅ 자동 복구 시퀀스 모든 단계 완료")
            self.logger.info("=" * 60)

            return True

        except Exception as e:
            self.logger.error(f"❌ 복구 시퀀스 실행 중 예외 발생: {e}", exc_info=True)
            return False

    def get_status(self) -> Dict[str, Any]:
        """
        자동 복구 관리자 상태 반환

        Returns:
            상태 정보 딕셔너리
        """
        return {
            'recovery_in_progress': self.recovery_in_progress,
            'total_recovery_count': self.recovery_count,
            'last_recovery_attempt': self.last_recovery_attempt.isoformat() if self.last_recovery_attempt else None
        }
