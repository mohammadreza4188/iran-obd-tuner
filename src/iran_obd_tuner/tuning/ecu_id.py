"""Mock ECU identification service for UDS simulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EcuIdentificationResult:
    vin: str
    ecu_family: str
    status: str
    simulated: bool


class MockEcuIdentifier:
    """Simulates UDS ReadDataByIdentifier (0x22) for VIN (0xF190)."""

    def identify(self) -> EcuIdentificationResult:
        """Return a simulated ECU identification response."""
        # Simulate a real VIN for an Iranian Peugeot Pars
        return EcuIdentificationResult(
            vin="IR1XXXXXXXXXXXXXX",  # Generic placeholder matching IR format
            ecu_family="Bosch ME17.9.7 (Simulated)",
            status="Read-Only (Safety Policy)",
            simulated=True,
        )


