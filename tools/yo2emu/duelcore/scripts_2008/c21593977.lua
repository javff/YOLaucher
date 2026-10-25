--処刑人－マキュラ
--Makyura the Destructor (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "During the turn this card is sent to the Graveyard, the owner of this
--card can activate Trap Card(s) from his/her hand."
--Diferencias con CardScripts (errata moderna): (1) fuera 'If this card is sent from the Monster Zone': vale venga de donde
--venga (campo, mano descartada, Deck...); (2) fuera 'You can only use this effect of "Makyura the Destructor" once per
--turn' y el 'activate 1 Trap Card' (SetCountLimit(1) del efecto de campo): se pueden activar TODAS las Trampas que se
--quieran de la mano ese turno; (3) no es un efecto que se active (sin cadena ni pregunta): es continuo, como el
--script 'Pre-Errata' de ProjectIgnis (pre-errata/c21593987.lua), que es la base de este.
local s,id=GetID()
function s.initial_effect(c)
	--activate trap in hand
	local e1=Effect.CreateEffect(c)
	e1:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_CONTINUOUS)
	e1:SetCode(EVENT_TO_GRAVE)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	local c=e:GetHandler()
	--el dueño de la carta
	local e1=Effect.CreateEffect(c)
	e1:SetType(EFFECT_TYPE_FIELD)
	e1:SetCode(EFFECT_TRAP_ACT_IN_HAND)
	e1:SetTargetRange(LOCATION_HAND,0)
	e1:SetReset(RESET_PHASE|PHASE_END)
	e1:SetDescription(aux.Stringid(id,0))
	Duel.RegisterEffect(e1,c:GetOwner())
end
