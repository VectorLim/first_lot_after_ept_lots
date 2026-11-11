import json
import logging
import os
from pathlib import Path
from cryptography.fernet import Fernet
from aed_client import ManufacturingService, aed_client

# vg2c: upstream `secret.read_API_KEY` and `pylog` replaced; credentials come from vg2c.runtime.aed_client.
logger = logging.getLogger(__name__)


def read_API_KEY(directory):
    raise ValueError(
        "Supply X_API_KEY, API_KEY/API_CONFIG, or an absolute AED_SECRETS_DIR"
    )


class AEDAPIClient:
    """
    A client for interacting with the AED Manufacturing Service.
    It handles API key reading, context loading, and service calls.
    """

    def __init__(self, facility: str):
        """
        Initializes the AEDAPIClient.

        Args:
            facility (str): The facility code (e.g., "A15").
        """
        self.facility = facility
        self._load_context_and_key()

    def _read_api_key(self):
        """Reuse X_API_KEY, or decrypt environment secrets before trying local files."""
        if os.environ.get("X_API_KEY"):
            return
        key = os.environ.get("API_KEY")
        encrypted_data = os.environ.get("API_CONFIG")
        if key is not None or encrypted_data is not None:
            if not key or not encrypted_data:
                raise ValueError(
                    "API_KEY and API_CONFIG must both contain encrypted-key configuration"
                )
            api_key = (
                Fernet(key.encode("utf-8"))
                .decrypt(encrypted_data.encode("utf-8"))
                .decode("utf-8")
            )
            if not api_key.strip():
                raise ValueError("Decrypted X_API_KEY must not be empty")
            os.environ["X_API_KEY"] = api_key
            return
        read_API_KEY(Path(__file__).resolve().parent.parent / "secret")

    def _load_context_and_key(self):
        """Loads the AED client context and reads the API key."""
        self._read_api_key()
        bypass = ",".join(
            (
                os.getenv("NO_PROXY", ""),
                os.getenv("no_proxy", ""),
                ".atmedispo.intel.com",
            )
        )
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(
            dict.fromkeys(host.strip() for host in bypass.split(",") if host.strip())
        )
        header = {
            "revision": "",
            "namespace": "atmedispo-icmpcs-warpage",
            "operation": "warpage",
            "application": "aed-icmpcs-warpage",
            "use_secondary_jobid": "false",
            "entity_id": "AED",
            "properties": [
                {"name": "env", "value": "PROD"},
                {"name": "facility", "value": self.facility},
                {"name": "protocol", "value": "HTTPS"},
            ],
        }
        logger.info(f"Loading context for facility: {self.facility}")
        aed_client.load_context(json.dumps(header))

    def lot_status(self, lot: str) -> dict:
        """
        Fetches the status for a specific lot.

        Args:
            lot (str): The lot identifier.

        Returns:
            dict: A dictionary containing the lot status information.

        Raises:
            Exception: If the API request fails.
        """
        try:
            logger.info(f"Fetching lot status for lot: {lot}")
            response = ManufacturingService().lot_status(lot)

            if hasattr(response, "raise_for_status"):
                response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
            elif response.status_code >= 400:
                logger.error(f"Request failed with status code: {response.status_code}")
                raise Exception(
                    f"Request failed with status code: {response.status_code}, body: {response.text}"
                )

            logger.info(f"Successfully fetched status for lot: {lot}")

            # Assuming response.json() is available, otherwise use json.loads(response.text)
            if hasattr(response, "json"):
                return response.json()
            else:
                return json.loads(response.text)

        except Exception as e:
            logger.error(f"Exception occurred while getting status for lot {lot}: {e}")
            raise

    def lot_hold(self, lotid: str, operation: str, holdcode: str, comment: str) -> dict:
        """Holds a lot."""
        try:
            logger.info(f"Holding lot {lotid} at operation {operation}")
            response = ManufacturingService().lot_hold(
                lotid, operation, holdcode, comment
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while holding lot {lotid}: {e}")
            raise

    def lot_move_in(self, lotid: str, operation: str) -> dict:
        """Moves a lot in."""
        try:
            logger.info(f"Moving in lot {lotid} at operation {operation}")
            response = ManufacturingService().lot_move_in(lotid, operation)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while moving in lot {lotid}: {e}")
            raise

    def lot_move_out(self, lotid: str, operation: str, quantity: int) -> dict:
        """Moves a lot out."""
        try:
            logger.info(f"Moving out lot {lotid} from operation {operation}")
            response = ManufacturingService().lot_move_out(lotid, operation, quantity)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while moving out lot {lotid}: {e}")
            raise

    def lot_release(self, lotid: str, operation: str, comment: str) -> dict:
        """Releases a lot."""
        try:
            logger.info(f"Releasing lot {lotid} at operation {operation}")
            response = ManufacturingService().lot_release(lotid, operation, comment)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while releasing lot {lotid}: {e}")
            raise

    def lot_contain(self, lotid: str, operation: str, reference_number: str) -> dict:
        """Contains a lot."""
        try:
            logger.info(f"Containing lot {lotid} at operation {operation}")
            response = ManufacturingService().lot_contain(
                lotid, operation, reference_number
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while containing lot {lotid}: {e}")
            raise

    def lot_uncontain(
        self, lotid: str, operation: str, reference_number: str, comment: str
    ) -> dict:
        """Uncontains a lot."""
        try:
            logger.info(f"Uncontaining lot {lotid} at operation {operation}")
            response = ManufacturingService().lot_uncontain(
                lotid, operation, reference_number, comment
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while uncontaining lot {lotid}: {e}")
            raise

    def entity_down(
        self, entity: str, module: str, componentid: str, description: str
    ) -> dict:
        """Sets an entity to down."""
        try:
            logger.info(f"Setting entity {entity} to down")
            response = ManufacturingService().entity_down(
                entity, module, componentid, description
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                f"Exception occurred while setting entity {entity} to down: {e}"
            )
            raise

    def lot_set_attr(self, lotid: str, attributeid: str, attributevalue: str) -> dict:
        """Sets a lot attribute."""
        try:
            logger.info(f"Setting attribute {attributeid} for lot {lotid}")
            response = ManufacturingService().lot_set_attr(
                lotid, attributeid, attributevalue
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                f"Exception occurred while setting attribute for lot {lotid}: {e}"
            )
            raise

    def lot_get_attr(self, lotid: str, attributeid: str) -> dict:
        """Gets a lot attribute."""
        try:
            logger.info(f"Getting attribute {attributeid} for lot {lotid}")
            response = ManufacturingService().lot_get_attr(lotid, attributeid)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                f"Exception occurred while getting attribute for lot {lotid}: {e}"
            )
            raise

    def contain_lots(
        self,
        commaseparatedlotlist: str,
        reference_number: str,
        disposition: str,
        commaseparatedoperationslist: str,
        emaillist: str,
        starttraceoperation: str,
    ) -> dict:
        """Contains multiple lots."""
        try:
            logger.info(f"Containing lots {commaseparatedlotlist}")
            response = ManufacturingService().contain_lots(
                commaseparatedlotlist,
                reference_number,
                disposition,
                commaseparatedoperationslist,
                emaillist,
                starttraceoperation,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Exception occurred while containing lots: {e}")
            raise

    def get_tester_traced_lots(
        self,
        lotid: str,
        tester_id: str,
        tiu_id: str,
        thermal_id: str,
        forward_offset: int,
        backward_offset: int,
    ) -> dict:
        """Gets tester traced lots."""
        try:
            logger.info(f"Getting tester traced lots for lot {lotid}")
            response = ManufacturingService().get_tester_traced_lots(
                lotid, tester_id, tiu_id, thermal_id, forward_offset, backward_offset
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                f"Exception occurred while getting tester traced lots for lot {lotid}: {e}"
            )
            raise
