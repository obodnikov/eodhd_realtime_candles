"""
Unit tests for manage_tickers.py script.
Tests API interactions, deduplication, capacity management, and error handling.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import os
import sys
import json
from pathlib import Path

import requests

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from manage_tickers import TickerManager, format_human_readable


class TestTickerManager(unittest.TestCase):
    """Test TickerManager class methods."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = TickerManager('http://localhost:8765', 'test-api-key')
    
    def test_init(self):
        """Test TickerManager initialization."""
        self.assertEqual(self.manager.api_url, 'http://localhost:8765')
        self.assertEqual(self.manager.api_key, 'test-api-key')
        self.assertEqual(self.manager.headers, {'X-API-Key': 'test-api-key'})
        self.assertEqual(self.manager.MAX_TICKERS, 50)
    
    @patch('manage_tickers.requests.get')
    def test_get_current_tickers_success(self, mock_get):
        """Test successful ticker retrieval."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'tickers': [
                {'symbol': 'AAPL', 'last_candle_request_at': '2025-01-01T10:00:00Z'},
                {'symbol': 'MSFT', 'last_candle_request_at': None}
            ]
        }
        mock_get.return_value = mock_response
        
        result = self.manager.get_current_tickers()
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['symbol'], 'AAPL')
        mock_get.assert_called_once_with(
            'http://localhost:8765/tickers',
            headers={'X-API-Key': 'test-api-key'},
            timeout=10
        )
    
    @patch('manage_tickers.requests.get')
    def test_get_current_tickers_missing_key(self, mock_get):
        """Test handling of missing 'tickers' key in response."""
        mock_response = Mock()
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response
        
        result = self.manager.get_current_tickers()
        
        self.assertEqual(result, [])
    
    @patch('manage_tickers.requests.delete')
    def test_remove_tickers_success(self, mock_delete):
        """Test successful ticker removal."""
        mock_response = Mock()
        mock_response.json.return_value = {'removed': ['AAPL', 'MSFT']}
        mock_delete.return_value = mock_response
        
        result = self.manager.remove_tickers(['AAPL', 'MSFT'])
        
        self.assertEqual(result['removed'], ['AAPL', 'MSFT'])
        mock_delete.assert_called_once()
    
    @patch('manage_tickers.requests.delete')
    def test_remove_tickers_missing_key(self, mock_delete):
        """Test handling of missing 'removed' key in response."""
        mock_response = Mock()
        mock_response.json.return_value = {}
        mock_delete.return_value = mock_response
        
        result = self.manager.remove_tickers(['AAPL'])
        
        self.assertEqual(result['removed'], [])
    
    @patch('manage_tickers.requests.post')
    def test_add_tickers_success(self, mock_post):
        """Test successful ticker addition."""
        mock_response = Mock()
        mock_response.json.return_value = {'added': ['AAPL', 'MSFT']}
        mock_post.return_value = mock_response
        
        result = self.manager.add_tickers(['AAPL', 'MSFT'])
        
        self.assertEqual(result['added'], ['AAPL', 'MSFT'])
        mock_post.assert_called_once()
    
    @patch('manage_tickers.requests.post')
    def test_add_tickers_missing_key(self, mock_post):
        """Test handling of missing 'added' key in response."""
        mock_response = Mock()
        mock_response.json.return_value = {}
        mock_post.return_value = mock_response
        
        result = self.manager.add_tickers(['AAPL'])
        
        self.assertEqual(result['added'], [])


class TestTickerManagerExecute(unittest.TestCase):
    """Test TickerManager.execute() method with various scenarios."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = TickerManager('http://localhost:8765', 'test-api-key')
    
    @patch.object(TickerManager, 'get_current_tickers')
    @patch.object(TickerManager, 'add_tickers')
    def test_execute_simple_addition(self, mock_add, mock_get):
        """Test simple addition with available capacity."""
        mock_get.return_value = [
            {'symbol': 'AAPL', 'last_candle_request_at': '2025-01-01T10:00:00Z'}
        ]
        mock_add.return_value = {'added': ['MSFT', 'GOOGL']}
        
        result = self.manager.execute(['MSFT', 'GOOGL'], force=False, dry_run=False)
        
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['summary']['added'], 2)
        self.assertEqual(result['summary']['removed'], 0)
        mock_add.assert_called_once_with(['MSFT', 'GOOGL'])
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_deduplication(self, mock_get):
        """Test input deduplication."""
        mock_get.return_value = []
        
        result = self.manager.execute(
            ['AAPL', 'MSFT', 'AAPL', 'GOOGL', 'MSFT'],
            force=False,
            dry_run=True
        )
        
        self.assertEqual(result['summary']['requested'], 5)
        self.assertEqual(result['summary']['unique'], 3)
        self.assertEqual(result['details']['duplicates_removed'], ['AAPL', 'MSFT'])
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_limit_to_50(self, mock_get):
        """Test limiting to 50 tickers."""
        mock_get.return_value = []
        input_tickers = [f'TICK{i}' for i in range(60)]
        
        result = self.manager.execute(input_tickers, force=False, dry_run=True)
        
        self.assertEqual(result['summary']['requested'], 60)
        self.assertEqual(result['summary']['limited_to'], 50)
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_already_tracked(self, mock_get):
        """Test skipping already tracked tickers."""
        mock_get.return_value = [
            {'symbol': 'AAPL', 'last_candle_request_at': '2025-01-01T10:00:00Z'},
            {'symbol': 'MSFT', 'last_candle_request_at': None}
        ]
        
        result = self.manager.execute(['AAPL', 'GOOGL'], force=False, dry_run=True)
        
        self.assertEqual(result['summary']['already_tracked'], 1)
        self.assertEqual(result['details']['already_tracked'], ['AAPL'])
        self.assertEqual(result['details']['to_add'], ['GOOGL'])
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_capacity_without_force(self, mock_get):
        """Test capacity management without force flag."""
        # System at 50 tickers
        mock_get.return_value = [
            {'symbol': f'TICK{i}', 'last_candle_request_at': None}
            for i in range(50)
        ]
        
        result = self.manager.execute(['NEW1', 'NEW2'], force=False, dry_run=False)
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('Need to remove', result['error'])
        self.assertEqual(result['summary']['to_remove'], 2)
    
    @patch.object(TickerManager, 'get_current_tickers')
    @patch.object(TickerManager, 'remove_tickers')
    @patch.object(TickerManager, 'add_tickers')
    def test_execute_capacity_with_force(self, mock_add, mock_remove, mock_get):
        """Test capacity management with force flag."""
        # System at 50 tickers
        mock_get.return_value = [
            {'symbol': f'OLD{i}', 'last_candle_request_at': None}
            for i in range(50)
        ]
        mock_remove.return_value = {'removed': ['OLD0', 'OLD1']}
        mock_add.return_value = {'added': ['NEW1', 'NEW2']}
        
        result = self.manager.execute(['NEW1', 'NEW2'], force=True, dry_run=False)
        
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['summary']['removed'], 2)
        self.assertEqual(result['summary']['added'], 2)
        mock_remove.assert_called_once()
        mock_add.assert_called_once()
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_removal_priority_null_first(self, mock_get):
        """Test removal prioritizes NULL last_candle_request_at."""
        # Removal only happens at capacity, so fill all MAX_TICKERS slots.
        mock_get.return_value = [
            {'symbol': 'TICK1', 'last_candle_request_at': '2025-01-05T10:00:00Z'},
            {'symbol': 'TICK2', 'last_candle_request_at': None},
            {'symbol': 'TICK3', 'last_candle_request_at': '2025-01-01T10:00:00Z'},
            {'symbol': 'TICK4', 'last_candle_request_at': None}
        ] + [
            {'symbol': f'FILL{i}', 'last_candle_request_at': '2025-06-01T10:00:00Z'}
            for i in range(TickerManager.MAX_TICKERS - 4)
        ]

        result = self.manager.execute(
            ['NEW1', 'NEW2', 'NEW3'],
            force=False,
            dry_run=True
        )
        
        # Should remove 3 tickers: NULL values first
        removed_symbols = [t['ticker'] for t in result['details']['to_remove']]
        self.assertEqual(len(removed_symbols), 3)
        # First two should be NULL values
        self.assertIn('TICK2', removed_symbols[:2])
        self.assertIn('TICK4', removed_symbols[:2])
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_removal_priority_oldest_timestamp(self, mock_get):
        """Test removal prioritizes oldest timestamps after NULLs."""
        # Removal only happens at capacity, and every ticker here has a
        # timestamp, so the oldest is the one that must go.
        mock_get.return_value = [
            {'symbol': 'TICK1', 'last_candle_request_at': '2025-01-05T10:00:00Z'},
            {'symbol': 'TICK2', 'last_candle_request_at': '2025-01-01T10:00:00Z'},
            {'symbol': 'TICK3', 'last_candle_request_at': '2025-01-03T10:00:00Z'}
        ] + [
            {'symbol': f'FILL{i}', 'last_candle_request_at': '2025-06-01T10:00:00Z'}
            for i in range(TickerManager.MAX_TICKERS - 3)
        ]

        result = self.manager.execute(['NEW1'], force=False, dry_run=True)
        
        # Should remove oldest: TICK2 (2025-01-01)
        removed = result['details']['to_remove'][0]
        self.assertEqual(removed['ticker'], 'TICK2')
        self.assertEqual(removed['reason'], 'oldest')
    
    @patch.object(TickerManager, 'get_current_tickers')
    def test_execute_dry_run_no_changes(self, mock_get):
        """Test dry-run mode makes no actual changes."""
        mock_get.return_value = []
        
        with patch.object(self.manager, 'add_tickers') as mock_add:
            result = self.manager.execute(['AAPL'], force=False, dry_run=True)
            
            self.assertTrue(result['dry_run'])
            mock_add.assert_not_called()


class TestFormatHumanReadable(unittest.TestCase):
    """Test format_human_readable function."""
    
    def test_format_success(self):
        """Test formatting successful result."""
        result = {
            'status': 'success',
            'dry_run': False,
            'summary': {
                'requested': 5,
                'unique': 3,
                'limited_to': 3,
                'already_tracked': 1,
                'to_add': 2,
                'to_remove': 0,
                'added': 2,
                'removed': 0
            },
            'details': {
                'already_tracked': ['AAPL'],
                'to_add': ['MSFT', 'GOOGL'],
                'to_remove': [],
                'duplicates_removed': ['AAPL', 'MSFT']
            }
        }
        
        output = format_human_readable(result)
        
        self.assertIn('Smart Ticker Management', output)
        self.assertIn('Requested: 5 tickers', output)
        self.assertIn('Unique: 3 tickers', output)
        self.assertIn('Added: 2 tickers', output)
    
    def test_format_error(self):
        """Test formatting error result."""
        result = {
            'status': 'error',
            'dry_run': False,
            'error': 'Need to remove 2 tickers. Use --force to proceed.',
            'summary': {
                'requested': 3,
                'unique': 3,
                'limited_to': 3,
                'already_tracked': 0,
                'to_add': 3,
                'to_remove': 2,
                'added': 0,
                'removed': 0
            },
            'details': {
                'already_tracked': [],
                'to_add': ['NEW1', 'NEW2', 'NEW3'],
                'to_remove': [
                    {'ticker': 'OLD1', 'last_request': None, 'reason': 'never_requested'},
                    {'ticker': 'OLD2', 'last_request': '2025-01-01T10:00:00Z', 'reason': 'oldest'}
                ],
                'duplicates_removed': []
            }
        }
        
        output = format_human_readable(result)
        
        self.assertIn('ERROR', output)
        self.assertIn('Need to remove 2 tickers', output)
        self.assertIn('OLD1', output)
        self.assertIn('never', output)
    
    def test_format_dry_run(self):
        """Test formatting dry-run result."""
        result = {
            'status': 'success',
            'dry_run': True,
            'summary': {
                'requested': 2,
                'unique': 2,
                'limited_to': 2,
                'already_tracked': 0,
                'to_add': 2,
                'to_remove': 0,
                'added': 2,
                'removed': 0
            },
            'details': {
                'already_tracked': [],
                'to_add': ['AAPL', 'MSFT'],
                'to_remove': [],
                'duplicates_removed': []
            }
        }
        
        output = format_human_readable(result)
        
        self.assertIn('DRY RUN MODE', output)
        self.assertIn('Would perform', output)


class TestMainFunction(unittest.TestCase):
    """Test main() function and CLI argument parsing."""
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch('sys.argv', ['manage_tickers.py', 'AAPL', 'MSFT'])
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_main_basic_execution(self, mock_dotenv, mock_manager_class):
        """Test basic main execution."""
        mock_manager = Mock()
        mock_manager.execute.return_value = {
            'status': 'success',
            'dry_run': False,
            'summary': {'requested': 2, 'unique': 2, 'limited_to': 2,
                       'already_tracked': 0, 'to_add': 2, 'to_remove': 0,
                       'added': 2, 'removed': 0},
            'details': {'already_tracked': [], 'to_add': ['AAPL', 'MSFT'],
                       'to_remove': [], 'duplicates_removed': []}
        }
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 0)
        mock_manager.execute.assert_called_once()
    
    @patch('sys.argv', ['manage_tickers.py', 'AAPL'])
    @patch.dict('os.environ', {}, clear=True)
    def test_main_missing_api_key(self):
        """Test main exits when API_KEY missing."""
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 3)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch('sys.argv', ['manage_tickers.py', 'AAPL', 'MSFT', 'GOOGL'])
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_main_connection_error(self, mock_dotenv, mock_manager_class):
        """Test main handles connection errors."""
        mock_manager = Mock()
        mock_manager.execute.side_effect = requests.exceptions.ConnectionError()
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 2)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch('sys.argv', ['manage_tickers.py', 'AAPL'])
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_main_http_401_error(self, mock_dotenv, mock_manager_class):
        """Test main handles 401 authentication errors."""
        mock_manager = Mock()
        mock_response = Mock()
        mock_response.status_code = 401
        http_error = requests.exceptions.HTTPError()
        http_error.response = mock_response
        mock_manager.execute.side_effect = http_error
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 3)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch('sys.argv', ['manage_tickers.py', 'AAPL'])
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_main_http_500_error(self, mock_dotenv, mock_manager_class):
        """Test main handles 500 server errors."""
        mock_manager = Mock()
        mock_response = Mock()
        mock_response.status_code = 500
        http_error = requests.exceptions.HTTPError()
        http_error.response = mock_response
        mock_manager.execute.side_effect = http_error
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 4)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch('sys.argv', ['manage_tickers.py', 'AAPL'])
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_main_unexpected_error(self, mock_dotenv, mock_manager_class):
        """Test main handles unexpected errors."""
        mock_manager = Mock()
        mock_manager.execute.side_effect = Exception('Unexpected error')
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with self.assertRaises(SystemExit) as cm:
            main()
        
        self.assertEqual(cm.exception.code, 4)


class TestTickerValidation(unittest.TestCase):
    """Test ticker validation logic."""
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_valid_tickers(self, mock_dotenv, mock_manager_class):
        """Test valid ticker formats are accepted."""
        mock_manager = Mock()
        mock_manager.execute.return_value = {
            'status': 'success', 'dry_run': True,
            'summary': {'requested': 6, 'unique': 6, 'limited_to': 6,
                       'already_tracked': 0, 'to_add': 6, 'to_remove': 0,
                       'added': 6, 'removed': 0},
            'details': {'already_tracked': [], 'to_add': ['AAPL', 'MSFT', 'BRK', 'GOOG', 'META', 'SPY'],
                       'to_remove': [], 'duplicates_removed': []}
        }
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        with patch('sys.argv', ['manage_tickers.py', '--dry-run', 'AAPL', 'msft', 'BRK', 'GOOG', 'META', 'SPY']):
            with self.assertRaises(SystemExit) as cm:
                main()
            
            self.assertEqual(cm.exception.code, 0)
            # Verify all tickers were passed (normalized to uppercase)
            call_args = mock_manager.execute.call_args[0]
            self.assertEqual(len(call_args[0]), 6)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    @patch('sys.stderr', new_callable=lambda: open(os.devnull, 'w'))
    def test_invalid_tickers_rejected(self, mock_stderr, mock_dotenv, mock_manager_class):
        """Test invalid ticker formats are rejected."""
        mock_manager = Mock()
        mock_manager.execute.return_value = {
            'status': 'success', 'dry_run': True,
            'summary': {'requested': 1, 'unique': 1, 'limited_to': 1,
                       'already_tracked': 0, 'to_add': 1, 'to_remove': 0,
                       'added': 1, 'removed': 0},
            'details': {'already_tracked': [], 'to_add': ['AAPL'],
                       'to_remove': [], 'duplicates_removed': []}
        }
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        # Invalid: too long, special chars, dots
        with patch('sys.argv', ['manage_tickers.py', '--dry-run', 'AAPL', 'TOOLONG', 'BRK.A', 'AA-BB', '']):
            with self.assertRaises(SystemExit) as cm:
                main()
            
            self.assertEqual(cm.exception.code, 0)
            # Only AAPL should be passed
            call_args = mock_manager.execute.call_args[0]
            self.assertEqual(call_args[0], ['AAPL'])
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_alphanumeric_tickers_accepted(self, mock_dotenv, mock_manager_class):
        """Test alphanumeric tickers are accepted."""
        mock_manager = Mock()
        mock_manager.execute.return_value = {
            'status': 'success', 'dry_run': True,
            'summary': {'requested': 2, 'unique': 2, 'limited_to': 2,
                       'already_tracked': 0, 'to_add': 2, 'to_remove': 0,
                       'added': 2, 'removed': 0},
            'details': {'already_tracked': [], 'to_add': ['ABC123', 'XYZ1'],
                       'to_remove': [], 'duplicates_removed': []}
        }
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        # Alphanumeric tickers (rare but valid)
        with patch('sys.argv', ['manage_tickers.py', '--dry-run', 'ABC123', 'XYZ1']):
            with self.assertRaises(SystemExit) as cm:
                main()
            
            self.assertEqual(cm.exception.code, 0)
            call_args = mock_manager.execute.call_args[0]
            self.assertEqual(len(call_args[0]), 2)
    
    @patch('manage_tickers.TickerManager')
    @patch('manage_tickers.load_dotenv')
    @patch.dict('os.environ', {'API_KEY': 'test-key'})
    def test_json_output_no_stderr_warning(self, mock_dotenv, mock_manager_class):
        """Test JSON mode doesn't print warnings to stderr."""
        mock_manager = Mock()
        mock_manager.execute.return_value = {
            'status': 'success', 'dry_run': True,
            'summary': {'requested': 1, 'unique': 1, 'limited_to': 1,
                       'already_tracked': 0, 'to_add': 1, 'to_remove': 0,
                       'added': 1, 'removed': 0},
            'details': {'already_tracked': [], 'to_add': ['AAPL'],
                       'to_remove': [], 'duplicates_removed': []}
        }
        mock_manager_class.return_value = mock_manager
        
        from manage_tickers import main
        
        # JSON mode should not print warnings
        with patch('sys.argv', ['manage_tickers.py', '--json', '--dry-run', 'AAPL', 'INVALID.TICKER']):
            with patch('sys.stderr') as mock_stderr:
                with self.assertRaises(SystemExit):
                    main()
                
                # stderr.write should not be called in JSON mode
                mock_stderr.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
