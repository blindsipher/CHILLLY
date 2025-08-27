"""Contract ID validation and normalization utilities.

Ensures all contract identifiers conform to ProjectX Gateway API requirements.
"""

import re
import logging
from typing import Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from topstepx_backend.services.contract_service import ContractService


@dataclass
class ContractValidationResult:
    """Result of contract ID validation."""
    
    is_valid: bool
    normalized_id: Optional[str]
    error_message: Optional[str] = None
    requires_lookup: bool = False


class ContractValidator:
    """
    Validates and normalizes contract identifiers according to ProjectX Gateway API specs.
    
    ProjectX requires full contract IDs in format: CON.F.US.{SYMBOL}.{MONTH}{YEAR}
    Example: CON.F.US.EP.U25 (E-mini S&P 500 September 2025)
    
    This validator:
    1. Validates full contract IDs
    2. Flags symbols that need API lookup
    3. Rejects malformed identifiers
    """
    
    # ProjectX contract ID pattern: CON.F.US.{SYMBOL}.{MONTH}{YEAR}
    CONTRACT_ID_PATTERN = re.compile(
        r'^CON\.[A-Z]+\.[A-Z]+\.[A-Z0-9]+\.[A-Z]\d{1,2}$'
    )
    
    # Month codes used in futures contracts
    MONTH_CODES = {
        'F': 'January',
        'G': 'February', 
        'H': 'March',
        'J': 'April',
        'K': 'May',
        'M': 'June',
        'N': 'July',
        'Q': 'August',
        'U': 'September',
        'V': 'October',
        'X': 'November',
        'Z': 'December'
    }
    
    # Common symbols that may be provided instead of full contract IDs
    COMMON_SYMBOLS = {
        'ES', 'NQ', 'YM', 'RTY', 'CL', 'GC', 'SI', 'ZB', 'ZN', 'ZC', 'ZS', 'ZW',
        'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'NZD', 'MXN',
        'EP', 'ENQ', 'EYM', 'ERX', 'ERW', 'ETF'  # E-mini symbols
    }
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def validate(self, identifier: str) -> ContractValidationResult:
        """
        Validate a contract identifier.
        
        Args:
            identifier: Contract ID or symbol to validate
            
        Returns:
            ContractValidationResult with validation status
        """
        if not identifier or not isinstance(identifier, str):
            return ContractValidationResult(
                is_valid=False,
                normalized_id=None,
                error_message="Contract identifier must be a non-empty string"
            )
        
        identifier = identifier.strip().upper()
        
        # Check if it's already a valid full contract ID
        if self.is_valid_contract_id(identifier):
            return ContractValidationResult(
                is_valid=True,
                normalized_id=identifier,
                requires_lookup=False
            )
        
        # Check if it's a known symbol that needs lookup
        if self.is_valid_symbol(identifier):
            return ContractValidationResult(
                is_valid=True,
                normalized_id=None,  # Will be resolved via API
                requires_lookup=True,
                error_message=f"Symbol '{identifier}' requires API lookup to get full contract ID"
            )
        
        # Check for common malformations
        error_msg = self._get_detailed_error(identifier)
        return ContractValidationResult(
            is_valid=False,
            normalized_id=None,
            error_message=error_msg
        )
    
    def is_valid_contract_id(self, contract_id: str) -> bool:
        """Check if string is a valid ProjectX contract ID."""
        return bool(self.CONTRACT_ID_PATTERN.match(contract_id))
    
    def is_valid_symbol(self, symbol: str) -> bool:
        """Check if string is a valid symbol that can be looked up."""
        # Accept known symbols
        if symbol in self.COMMON_SYMBOLS:
            return True
        
        # Accept 2-6 character alphanumeric symbols
        if 2 <= len(symbol) <= 6 and symbol.isalnum():
            return True
        
        return False
    
    def parse_contract_id(self, contract_id: str) -> Optional[dict]:
        """
        Parse a valid contract ID into its components.
        
        Returns:
            Dict with parsed components or None if invalid
        """
        if not self.is_valid_contract_id(contract_id):
            return None
        
        parts = contract_id.split('.')
        
        # Extract month and year from the last part
        expiry_code = parts[-1]
        month_code = expiry_code[0]
        year = expiry_code[1:]
        
        return {
            'full_id': contract_id,
            'type': parts[1],  # F for Futures
            'market': parts[2],  # US
            'symbol': parts[3],  # EP, ENQ, etc.
            'month_code': month_code,
            'month_name': self.MONTH_CODES.get(month_code, 'Unknown'),
            'year': year,
            'expiry_code': expiry_code
        }
    
    def _get_detailed_error(self, identifier: str) -> str:
        """Generate detailed error message for invalid identifier."""
        # Check for common issues
        if '..' in identifier:
            return f"Invalid contract ID '{identifier}': Contains empty segments (double dots)"
        
        if identifier.startswith('CON.'):
            parts = identifier.split('.')
            if len(parts) != 5:
                return (
                    f"Invalid contract ID '{identifier}': "
                    f"Expected 5 parts (CON.TYPE.MARKET.SYMBOL.EXPIRY), got {len(parts)}"
                )
            
            # Validate expiry code
            if len(parts) == 5:
                expiry = parts[4]
                if not expiry or not expiry[0].isalpha() or not expiry[1:].isdigit():
                    return (
                        f"Invalid contract ID '{identifier}': "
                        f"Invalid expiry code '{expiry}' (expected format: M25 where M=month, 25=year)"
                    )
        
        # Generic error
        return (
            f"Invalid contract identifier '{identifier}': "
            f"Must be either a full contract ID (CON.F.US.EP.U25) or a valid symbol (ES, NQ, etc.)"
        )
    
    async def normalize_identifier(
        self, 
        identifier: str,
        contract_service: Optional['ContractService'] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Normalize a contract identifier to full contract ID.
        
        Args:
            identifier: Contract ID or symbol to normalize
            contract_service: Optional contract service for API lookups
            
        Returns:
            Tuple of (success, normalized_id, error_message)
        """
        validation = self.validate(identifier)
        
        if not validation.is_valid:
            return False, None, validation.error_message
        
        # Already normalized
        if validation.normalized_id:
            return True, validation.normalized_id, None
        
        # Needs API lookup
        if validation.requires_lookup and contract_service:
            try:
                # Search for the symbol
                contracts = await contract_service.search_contracts_api(
                    identifier, 
                    live=True
                )
                
                if contracts:
                    # Use the first active contract
                    for contract in contracts:
                        if contract.is_active:
                            self.logger.info(
                                f"Resolved symbol '{identifier}' to contract ID '{contract.id}'"
                            )
                            return True, contract.id, None
                    
                    # No active contracts, use first one
                    if contracts:
                        contract = contracts[0]
                        self.logger.warning(
                            f"No active contracts for '{identifier}', using '{contract.id}'"
                        )
                        return True, contract.id, None
                
                error_msg = f"No contracts found for symbol '{identifier}'"
                self.logger.warning(error_msg)
                return False, None, error_msg
                
            except Exception as e:
                error_msg = f"Failed to lookup symbol '{identifier}': {e}"
                self.logger.error(error_msg)
                return False, None, error_msg
        
        # No contract service available for lookup
        if validation.requires_lookup:
            return False, None, "Symbol lookup requires contract service"
        
        return False, None, "Unexpected validation state"


# Singleton instance for easy import
contract_validator = ContractValidator()