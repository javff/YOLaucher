--ネクロ・ガードナー
--Necro Gardna (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "Remove from play this card in your Graveyard to negate 1 attack from a
--monster your opponent controls."
--Diferencia con CardScripts (errata moderna): el texto de hoy dice 'During your opponent's turn: ... negate the NEXT attack
--this turn', y el script deja activarlo en cualquier momento del turno del rival antes del combate (Main Phase 1,
--Battle Start...) dejando un efecto que anula la siguiente declaracion de ataque. En 2008 solo se anula 'un ataque de un
--monstruo del rival': hay que activarlo con un ataque ya declarado (Quick Effect al declararse el ataque). Sin ataque en
--curso no se ofrece, y no deja nada registrado.
local s,id=GetID()
function s.initial_effect(c)
	--disable attack
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetType(EFFECT_TYPE_QUICK_O)
	e1:SetCode(EVENT_FREE_CHAIN)
	e1:SetHintTiming(0,TIMING_ATTACK)
	e1:SetRange(LOCATION_GRAVE)
	e1:SetCondition(s.condition)
	e1:SetCost(Cost.SelfBanish)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
function s.condition(e,tp,eg,ep,ev,re,r,rp)
	local at=Duel.GetAttacker()
	return Duel.IsTurnPlayer(1-tp) and at~=nil and at:IsControler(1-tp)
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	Duel.NegateAttack()
end
