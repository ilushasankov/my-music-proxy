from aiogram.fsm.state import State, StatesGroup

class DonationStates(StatesGroup):
    waiting_for_amount = State() 